import os
import json
import time
import requests
from requests.exceptions import Timeout, RequestException

from helpers.helpers import run_command
from logger import setup_logger

# Initialize logger for GitHub integration
logger = setup_logger(__name__)


def gather_github_pr_context():
    # git diff origin main
    changed_files_raw = run_command(["git", "diff", "--name-only", "origin/main...HEAD"])
    files = [f for f in changed_files_raw.split("\n") if f.lower().endswith((".yaml", ".yml")) and os.path.exists(f)]

    raw_manifests = {}
    git_diffs = {}

    for file_path in files:
        # get YAML manifest files
        with open(file_path, "r", encoding="utf-8") as f:
            raw_manifests[file_path] = f.read()

        # get the git diff per file_path
        diff_content = run_command(["git", "diff", "origin/main...HEAD", "--", file_path])
        git_diffs[file_path] = diff_content

    return raw_manifests, git_diffs


def _diff_added_lines(unified_diff: str) -> set:
    added_lines = set()
    current_new_line = None

    for raw_line in unified_diff.splitlines():
        line = raw_line.rstrip("\n")

        if line.startswith("@@"):
            # Example: @@ -10,2 +42,5 @@
            plus_index = line.find("+")
            if plus_index == -1:
                current_new_line = None
                continue

            hunk_meta = line[plus_index + 1:].split(" ", 1)[0]
            start = hunk_meta.split(",", 1)[0]
            try:
                current_new_line = int(start)
            except ValueError:
                current_new_line = None
            continue

        if current_new_line is None:
            continue

        if line.startswith("+") and not line.startswith("+++"):
            added_lines.add(current_new_line)
            current_new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith("\\"):
            continue
        else:
            current_new_line += 1

    return added_lines


def _safe_violation_value(violation, field_name: str, fallback):
    if hasattr(violation, field_name):
        return getattr(violation, field_name)
    if isinstance(violation, dict):
        return violation.get(field_name, fallback)
    return fallback


def _post_with_retry(url: str, payload: dict, headers: dict, max_retries: int = 3) -> requests.Response:
    """
    Post to GitHub API with exponential backoff retry logic for rate limits and transient failures.
    
    Args:
        url: GitHub API endpoint URL
        payload: JSON payload to send
        headers: Request headers including authorization
        max_retries: Maximum number of retry attempts (default: 3)
    
    Returns:
        Response object from successful request
        
    Raises:
        RuntimeError: If all retries exhausted or non-retryable error encountered
    """
    last_response = None
    
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            last_response = response
            
            # Success cases
            if response.status_code in [200, 201]:
                return response
            
            # Rate limit - honor Retry-After header
            if response.status_code == 429:
                retry_after_header = response.headers.get("Retry-After", "60")
                try:
                    # Try parsing as integer seconds first
                    retry_after = int(retry_after_header)
                except ValueError:
                    # Parse HTTP-date format (RFC 7231) and calculate seconds from now
                    from email.utils import parsedate_to_datetime
                    from datetime import datetime, timezone
                    try:
                        retry_date = parsedate_to_datetime(retry_after_header)
                        retry_after = max(int((retry_date - datetime.now(timezone.utc)).total_seconds()), 1)
                    except Exception:
                        # Fallback if date parsing fails
                        retry_after = 60
                
                # Cap retry_after to prevent indefinite hangs (max 5 minutes)
                retry_after = min(retry_after, 300)
                
                if attempt < max_retries - 1:
                    logger.warning(f"Rate limited by GitHub API. Retrying after {retry_after} seconds...")
                    time.sleep(retry_after)
                    continue
                else:
                    raise RuntimeError(f"GitHub API rate limit exceeded after {max_retries} attempts")
            
            # Server errors - retry with exponential backoff
            if response.status_code in [500, 502, 503, 504]:
                if attempt < max_retries - 1:
                    backoff = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(f"GitHub API server error {response.status_code}. Retrying in {backoff}s...")
                    time.sleep(backoff)
                    continue
                else:
                    safe_response = response.text[:500] if response.text and len(response.text) > 500 else response.text
                    raise RuntimeError(
                        f"GitHub API server error persisted after {max_retries} attempts: "
                        f"{response.status_code} - {safe_response}"
                    )
            
            # Non-retryable errors (4xx client errors except 429) - return response for caller to handle
            if 400 <= response.status_code < 500:
                return response
            
            # Unexpected status codes - return for caller to handle
            return response
            
        except Timeout:
            if attempt < max_retries - 1:
                backoff = 2 ** attempt
                logger.warning(f"Request timeout. Retrying in {backoff}s...")
                time.sleep(backoff)
                continue
            else:
                raise RuntimeError(f"GitHub API request timed out after {max_retries} attempts")
        
        except RequestException as exc:
            if attempt < max_retries - 1:
                backoff = 2 ** attempt
                logger.warning(f"Request failed: {exc}. Retrying in {backoff}s...")
                time.sleep(backoff)
                continue
            else:
                raise RuntimeError(f"Failed to post to GitHub API after {max_retries} attempts: {exc}") from exc
    
    # Should never reach here, but safety fallback
    # If we somehow exit the loop without returning or raising, return last response or raise
    if last_response is not None:
        return last_response
    raise RuntimeError("Unexpected error in retry logic: no response received")


def prepare_review_to_github(verdict: str, summary: str, violations: list, git_diffs: dict):
    github_event = "APPROVE" if verdict == "APPROVE" and not violations else "REQUEST_CHANGES"
    anchorable_lines = {
        path: _diff_added_lines(diff_text)
        for path, diff_text in git_diffs.items()
    }

    # 1. individual inline comments
    comments = []
    dropped_inline_comment_count = 0
    for v in violations:
        file_path = _safe_violation_value(v, "file_path", "")
        severity = _safe_violation_value(v, "severity", "CRITICAL")
        resource_kind = _safe_violation_value(v, "resource_kind", "Resource")
        resource_name = _safe_violation_value(v, "resource_name", "Unknown")
        finding = _safe_violation_value(v, "finding", "")
        remediation = _safe_violation_value(v, "remediation", "")
        target_line = _safe_violation_value(v, "target_line", 1)

        try:
            target_line = int(target_line)
        except (TypeError, ValueError):
            target_line = 1

        body_markdown = (
            f"### AI Reviewer Finding: `{severity}`\n"
            f"**Resource:** `{resource_kind}/{resource_name}`\n\n"
            f"{finding}\n\n"
            f"#### Suggested Remediation:\n```yaml\n{remediation}\n```"
        )
        file_anchorable_lines = anchorable_lines.get(file_path, set())
        if target_line > 0 and target_line in file_anchorable_lines:
            comments.append({
                "path": file_path,
                "body": body_markdown,
                "line": target_line,
                "side": "RIGHT"
            })
        else:
            dropped_inline_comment_count += 1

    review_body = (
        f"## GitOps K8s AI Review Summary\n"
        f"**Verdict:** {verdict}\n\n"
        f"{summary}\n\n"
        f"*Total Violations Flagged: {len(violations)}*"
    )
    if dropped_inline_comment_count:
        review_body += (
            f"\n\n*Note: {dropped_inline_comment_count} finding(s) were included in the summary only "
            f"because they could not be safely anchored to changed lines in this diff.*"
        )

    return github_event, review_body, comments


def write_pr_review(github_event, review_body, comments):
    repo = os.getenv("GITHUB_REPOSITORY")
    event_path = os.getenv("GITHUB_EVENT_PATH")
    token = os.getenv("GITHUB_TOKEN")

    if not repo or not event_path or not token:
        raise RuntimeError("Missing required GitHub runtime variables (GITHUB_REPOSITORY, GITHUB_EVENT_PATH, GITHUB_TOKEN).")

    with open(event_path, "r", encoding="utf-8") as f:
        event_data = json.load(f)

    pr_number = event_data["pull_request"]["number"]


    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/reviews"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    payload = {
        "body": review_body,
        "event": github_event,
        "comments": comments
    }
    
    # Attempt primary review post with retry logic
    response = _post_with_retry(url, payload, headers)

    if response.status_code in [200, 201]:
        logger.info("Successfully posted Review to GitHub.")
        return

    # Handle invalid inline comment anchors (422) - fallback to summary-only review
    if response.status_code == 422 and comments:
        logger.warning("GitHub rejected inline comment anchors. Attempting summary-only review...")
        fallback_payload = {
            "body": f"{review_body}\n\n*Inline findings were omitted because GitHub rejected line anchors.*",
            "event": github_event
        }
        fallback_response = _post_with_retry(url, fallback_payload, headers)
        
        if fallback_response.status_code in [200, 201]:
            logger.info("Successfully posted summary-only review after inline anchor validation failure.")
            return
        raise RuntimeError(
            f"GitHub review fallback failed with status {fallback_response.status_code}: {fallback_response.text}"
        )

    raise RuntimeError(f"Failed to post review. Server responded with status {response.status_code}: {response.text}")
