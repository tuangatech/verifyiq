"""LangGraph state machine for the Employment Verification workflow."""

from langgraph.graph import StateGraph, END

from .graph_state import EmploymentGraphState
from .nodes import (
    lookup_current_employer,
    lookup_prior_employer,
    verify_income,
    calculate_tenure,
    build_artifact,
)


def build_employment_graph() -> StateGraph:
    """Construct and compile the Employment Verification graph.

    Returns a compiled, stateless graph ready for reuse across requests.
    """
    graph = StateGraph(EmploymentGraphState)

    # Add all five nodes
    graph.add_node("lookup_current_employer", lookup_current_employer)
    graph.add_node("lookup_prior_employer", lookup_prior_employer)
    graph.add_node("verify_income", verify_income)
    graph.add_node("calculate_tenure", calculate_tenure)
    graph.add_node("build_artifact", build_artifact)

    # Set entry point
    graph.set_entry_point("lookup_current_employer")

    # Build conditional edges:
    # - After lookup_current_employer, route based on currently_employed
    # - If True: go directly to verify_income
    # - If False: go to lookup_prior_employer
    # - If error: go to build_artifact (terminal path)
    def route_after_employer_check(state: EmploymentGraphState) -> str:
        """Route based on employment status."""
        if state.get("error"):
            return "build_artifact"
        if state.get("currently_employed"):
            return "verify_income"
        return "lookup_prior_employer"

    # Add conditional edge with route map
    graph.add_conditional_edges(
        "lookup_current_employer",
        route_after_employer_check,
        {
            "verify_income": "verify_income",
            "lookup_prior_employer": "lookup_prior_employer",
            "build_artifact": "build_artifact",
        },
    )

    # Add edges from lookup_prior_employer to verify_income
    graph.add_edge("lookup_prior_employer", "verify_income")

    # After verify_income, always calculate tenure
    graph.add_edge("verify_income", "calculate_tenure")

    # After calculate_tenure, build final artifact
    graph.add_edge("calculate_tenure", "build_artifact")

    # build_artifact is the terminal node
    graph.add_edge("build_artifact", END)

    return graph.compile()
