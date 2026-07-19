import subprocess

from state import ReviewState


# run shell commands within GitHub runner
def run_command(cmd: list) -> str:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    return result.stdout.strip()


def evaluate_static_gate(state: ReviewState) -> str:
    if state.get("static_errors"):
        return "block_pipeline"
    return "execute_agents"
