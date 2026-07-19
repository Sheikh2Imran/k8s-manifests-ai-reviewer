import os
import json
import requests

from helpers.helpers import run_command


def gather_github_pr_context():
    # git diff origin main
    changed_files_raw = run_command(["git", "diff", "--name-only", "origin/main...HEAD"])
    files = [f for f in changed_files_raw.split("\n") if f.endswith((".yaml", ".yml")) and os.path.exists(f)]

    raw_manifests = {}
    git_diffs = {}

    for file_path in files:
        # get YAML manifest files
        with open(file_path, "r") as f:
            raw_manifests[file_path] = f.read()

        # get the git diff per file_path
        diff_content = run_command(["git", "diff", "origin/main...HEAD", "--", file_path])
        git_diffs[file_path] = diff_content

    return raw_manifests, git_diffs


def prepare_review_to_github(verdict: str, summary: str, violations: list):
    github_event = "APPROVE" if verdict == "APPROVE" and not violations else "REQUEST_CHANGES"

    # 1. individual inline comments
    comments = []
    for v in violations:
        body_markdown = (
            f"### 🤖 AI Reviewer Finding: `{v.severity}`\n"
            f"**Resource:** `{v.resource_kind}/{v.resource_name}`\n\n"
            f"{v.finding}\n\n"
            f"#### 🛠️ Suggested Remediation:\n```yaml\n{v.remediation}\n```"
        )
        comments.append({
            "path": v.file_path,
            "body": body_markdown,
            "position": 1
        })

    review_body = (
        f"## 🛡️ GitOps K8s AI Review Summary\n"
        f"**Verdict:** {verdict}\n\n"
        f"{summary}\n\n"
        f"*Total Violations Flagged: {len(violations)}*"
    )

    return github_event, review_body, comments


def write_pr_review(github_event, review_body, comments):
    repo = os.getenv("GITHUB_REPOSITORY")
    event_path = os.getenv("GITHUB_EVENT_PATH")
    token = os.getenv("GITHUB_TOKEN")

    if not event_path or not token:
        print("Missing GITHUB_EVENT_PATH or GITHUB_TOKEN. Skipping GitHub API posting.")
        return

    with open(event_path, "r") as f:
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
    response = requests.post(url, json=payload, headers=headers)

    if response.status_code == 201:
        print("Successfully posted Review to GitHub.")
    else:
        print(f"Failed to post review. Server responded with status {response.status_code}: {response.text}")
