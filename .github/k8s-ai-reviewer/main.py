import sys
import traceback
from helpers.helpers import run_command
from logger import setup_logger
from pipeline import build_review_workflow
from tools.github import (
    gather_github_pr_context,
    write_pr_review,
    prepare_review_to_github
)

logger = setup_logger(__name__)


if __name__ == "__main__":
    try:
        # git fetch origin main
        try:
            run_command(["git", "fetch", "origin", "main"])
        except Exception as e:
            logger.warning(f"Warning during git fetch context building: {e}")

        raw_manifests, git_diffs = gather_github_pr_context()

        if not raw_manifests:
            logger.info("No Kubernetes YAML manifest files changed in this Pull Request. Exiting gracefully.")
            sys.exit(0)

        logger.info(f"Discovered {len(raw_manifests)} modified manifest(s). Invoking LangGraph core...")

        app_graph = build_review_workflow()

        initial_inputs = {
            "raw_manifests": raw_manifests,
            "git_diffs": git_diffs,
            "aggregated_violations": [],
            "static_errors": []
        }

        runtime_output = app_graph.invoke(initial_inputs)
        
        logger.debug(f"Runtime output keys: {runtime_output.keys()}")
        logger.info(f"Violations count: {len(runtime_output.get('aggregated_violations', []))}")

        # Route output to GitHub API Router
        github_event, review_body, comments = prepare_review_to_github(
            verdict=runtime_output.get("final_verdict", "REQUEST_CHANGES"),
            summary=runtime_output.get("executive_summary", ""),
            violations=runtime_output.get("aggregated_violations", []),
            git_diffs=git_diffs
        )
        write_pr_review(github_event, review_body, comments)

        # Enforce build failure at the CI layer if the agent rejects the changes
        if runtime_output.get("final_verdict") == "REQUEST_CHANGES":
            logger.warning("Agent issued a REQUEST_CHANGES verdict. Blocking PR merge pipeline step.")
            sys.exit(1)
        else:
            logger.info("Agent approved manifest configurations.")
            sys.exit(0)
            
    except KeyboardInterrupt:
        logger.info("Review interrupted by user.")
        sys.exit(130)  # Standard exit code for SIGINT
        
    except Exception as e:
        logger.error("=" * 80)
        logger.error("FATAL ERROR: AI Reviewer encountered an unrecoverable error")
        logger.error("=" * 80)
        logger.error(f"Error: {e}")
        logger.error("Stack trace:")
        logger.error(traceback.format_exc())
        logger.error("=" * 80)
        logger.error("This PR cannot be reviewed automatically. Please:")
        logger.error("1. Check the error message above for details")
        logger.error("2. Verify manifest YAML syntax is valid")
        logger.error("3. Contact platform team if issue persists")
        logger.error("=" * 80)
        # CRITICAL: Exit with error code 1 to fail the CI build
        sys.exit(1)
