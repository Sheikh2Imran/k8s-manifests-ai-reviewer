from typing import TypedDict, List, Dict, Annotated
from operator import add
from schema import ViolationItem


class ReviewState(TypedDict):
    # Git context inputs
    raw_manifests: Dict[str, str]
    git_diffs: Dict[str, str]

    # Preprocessed states
    sanitized_manifests: Dict[str, str]
    static_errors: List[str]

    aggregated_violations: Annotated[List[ViolationItem], add]

    # Final outputs
    final_verdict: str
    executive_summary: str
