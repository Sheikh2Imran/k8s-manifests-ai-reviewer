from langgraph.graph import StateGraph, START, END

from helpers.helpers import evaluate_static_gate
from state import ReviewState
from agents import (
    preprocess_sanitizer_node,
    security_agent_node,
    reliability_agent_node,
    resource_agent_node,
    final_orchestration_node
)


def build_review_workflow():
    builder = StateGraph(ReviewState)

    # 1. Register Node Matrix
    builder.add_node("preprocess_sanitizer", preprocess_sanitizer_node)
    builder.add_node("security_reviewer", security_agent_node)
    builder.add_node("reliability_reviewer", reliability_agent_node)
    builder.add_node("resource_reviewer", resource_agent_node)
    builder.add_node("consolidator", final_orchestration_node)

    # 2. Map Edge Dependencies
    builder.add_edge(START, "preprocess_sanitizer")

    builder.add_conditional_edges(
        "preprocess_sanitizer",
        evaluate_static_gate,
        {
            "block_pipeline": "consolidator",
            "execute_agents": "security_reviewer"
        }
    )

    builder.add_edge("security_reviewer", "reliability_reviewer")
    builder.add_edge("reliability_reviewer", "resource_reviewer")
    builder.add_edge("resource_reviewer", "consolidator")
    builder.add_edge("consolidator", END)

    return builder.compile()
