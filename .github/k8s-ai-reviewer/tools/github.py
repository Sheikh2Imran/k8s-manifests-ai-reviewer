import os
import json
import requests
from helpers.helpers import run_command
from logger import setup_logger

logger = setup_logger(__name__)


def gather_github_pr_context():
    changed_files_raw = run_command(["git", "diff", "--name-only", "origin/main...HEAD"])
    files = [f for f in changed_files_raw.split("\n") if f.lower().endswith((".yaml", ".yml")) and os.path.exists(f)]

    raw_manifests, git_diffs = {}, {}
    for file_path in files:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_manifests[file_path] = f.read()
        git_diffs[file_path] = run_command(["git", "diff", "origin/main...HEAD", "--", file_path])

    return raw_manifests, git_diffs


def _safe_violation_value(violation, field_name: str, fallback):
    if hasattr(violation, field_name):
        return getattr(violation, field_name)
    return violation.get(field_name, fallback) if isinstance(violation, dict) else fallback


def prepare_review_to_github(verdict: str, summary: str, violations: list):
    logger.info(f"Formatting {len(violations)} raw architecture findings.")
    github_event = "APPROVE" if verdict == "APPROVE" and not violations else "REQUEST_CHANGES"

    comments = []
    detailed_findings_markdown = ""

    for idx, v in enumerate(violations, 1):
        file_path = _safe_violation_value(v, "file_path", "")
        severity = _safe_violation_value(v, "severity", "CRITICAL")
        kind = _safe_violation_value(v, "resource_kind", "Resource")
        name = _safe_violation_value(v, "resource_name", "Unknown")
        finding = _safe_violation_value(v, "finding", "")
        remediation = _safe_violation_value(v, "remediation", "")

        try:
            target_line = int(_safe_violation_value(v, "target_line", 1))
        except (TypeError, ValueError):
            target_line = 1

        body_markdown = (
            f"### AI Reviewer Finding: `{severity}`\n"
            f"**Resource:** `{kind}/{name}`\n\n"
            f"{finding}\n\n"
            f"#### Suggested Remediation:\n```yaml\n{remediation}\n```"
        )

        # Append inline tracking structure
        comments.append({
            "path": file_path,
            "body": body_markdown,
            "line": target_line,
            "side": "RIGHT"
        })

        # Pre-build summary description to handle fallback cases smoothly
        detailed_findings_markdown += (
            f"### {idx}. `{severity}` - {file_path}:{target_line}\n"
            f"**Resource:** `{kind}/{name}`\n\n"
            f"{finding}\n\n"
            f"**Suggested Remediation:**\n```yaml\n{remediation}\n```\n\n---\n\n"
        )

    review_body = (
        f"## GitOps K8s AI Review Summary\n"
        f"**Verdict:** {verdict}\n\n"
        f"{summary}\n\n"
        f"*Total Violations Flagged: {len(violations)}*"
    )

    return github_event, review_body, comments, detailed_findings_markdown


def write_pr_review(github_event, review_body, comments, detailed_findings_markdown):
    repo = os.getenv("GITHUB_REPOSITORY")
    event_path = os.getenv("GITHUB_EVENT_PATH")
    token = os.getenv("GITHUB_TOKEN")

    if not (repo and event_path and token):
        raise RuntimeError("Missing essential runner metadata context (REPO/EVENT/TOKEN).")

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

    logger.info(f"Posting structural payload to PR #{pr_number}...")
    response = requests.post(url, json=payload, headers=headers, timeout=30)

    # 1. Success validation catch
    if response.status_code in [200, 201]:
        logger.info("Successfully posted Inline Review payload to GitHub.")
        return

    # 2. Validation fallback catch (If LLM targets lines outside the modified diff)
    if response.status_code == 422:
        logger.warning(
            "Line anchors fell outside the patch diff window. Falling back to review summary payload layout...")

        fallback_body = f"{review_body}\n\n## Detailed Findings\n\n{detailed_findings_markdown}"
        fallback_payload = {
            "body": fallback_body,
            "event": github_event
        }

        fallback_resp = requests.post(url, json=fallback_payload, headers=headers, timeout=30)
        if fallback_resp.status_code in [200, 201]:
            logger.info("Successfully posted summary review containing aggregated inline data details.")
            return

        raise RuntimeError(f"Fallback post failed with status code {fallback_resp.status_code}: {fallback_resp.text}")

    raise RuntimeError(f"GitHub pipeline post failure ({response.status_code}): {response.text}")
