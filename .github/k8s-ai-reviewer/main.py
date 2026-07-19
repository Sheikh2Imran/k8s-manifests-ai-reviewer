from helpers.helpers import run_command
from pipeline import build_review_workflow
from tools.github import (
    gather_github_pr_context,
    write_pr_review,
    prepare_review_to_github
)


if __name__ == "__main__":
    # git fetch origin main
    try:
        run_command(["git", "fetch", "origin", "main"])
    except Exception as e:
        print(f"Warning during git fetch context building: {e}")

    raw_manifests, git_diffs = gather_github_pr_context()

    if not raw_manifests:
        print("No Kubernetes YAML manifest files changed in this Pull Request. Exiting gracefully.")
        exit(0)

    print(f"Discovered {len(raw_manifests)} modified manifest(s). Invoking LangGraph core...")

    app_graph = build_review_workflow()

    initial_inputs = {
        "raw_manifests": raw_manifests,
        "git_diffs": git_diffs,
        "aggregated_violations": [],
        "static_errors": []
    }

    runtime_output = app_graph.invoke(initial_inputs)

    # Route output to GitHub API Router
    github_event, review_body, comments = prepare_review_to_github(
        verdict=runtime_output.get("final_verdict", "REQUEST_CHANGES"),
        summary=runtime_output.get("executive_summary", ""),
        violations=runtime_output.get("aggregated_violations", [])
    )
    write_pr_review(github_event, review_body, comments)

    # Enforce build failure at the CI layer if the agent rejects the changes
    if runtime_output.get("final_verdict") == "REQUEST_CHANGES":
        print("Agent issued a REQUEST_CHANGES verdict. Blocking PR merge pipeline step.")
        exit(1)
    else:
        print("Agent approved manifest configurations.")
        exit(0)
