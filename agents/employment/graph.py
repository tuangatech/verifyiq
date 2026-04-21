"""LangGraph state machine for the Employment Verification workflow."""

from typing import TypedDict, Union, List

from langgraph.graph import StateGraph, END

from .graph_state import EmploymentGraphState
from .nodes import (
    lookup_current_employer,
    lookup_prior_employer,
    verify_income,
    calculate_tenure,
    build_artifact,
)


class EmploymentState(TypedDict):
    """Internal state dict for LangGraph compilation.

    Mirrors EmploymentGraphState TypedDict but extends it to include
    intermediate values needed for routing (currently_employed for conditional edge).
    """
    subject_id: str
    subject_name: str
    use_case: str
    today: str
    currently_employed: bool
    employer_name: str
    employment_start_date: str
    employment_type: str
    prior_employer_name: str
    prior_employer_end_date: str
    verified_annual_income: int
    income_currency: str
    tenure_years: float
    verification_confidence: str
    artifact: dict
    error: str


def build_employment_graph() -> StateGraph:
    """Construct and compile the Employment Verification graph.

    Returns a compiled, stateless graph ready for reuse across requests.
    """
    # Create graph with EmploymentState as the type hint
    graph = StateGraph(EmploymentState)

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
    def route_after_employer_check(
        state: EmploymentState
    ) -> Union[str, List[str]]:
        """Route based on employment status.

        Returns either "verify_income", "lookup_prior_employer", "build_artifact", or "END".
        """
        if state.get("error"):
            return "build_artifact"
        if state.get("currently_employed"):
            return "verify_income"
        return ["lookup_prior_employer", "verify_income"]

    # Add conditional edge with route map
    graph.add_conditional_edges(
        "lookup_current_employer",
        route_after_employer_check,
        {
            "verify_income": "verify_income",
            "lookup_prior_employer": "lookup_prior_employer",
            "build_artifact": "build_artifact",
            "END": END,
        },
    )

    # Add edges from lookup_prior_employer to verify_income
    graph.add_edge("lookup_prior_employer", "verify_income")

    # After verify_income, always calculate tenure
    graph.add_edge("verify_income", "calculate_tenure")

    # After calculate_tenure, build final artifact
    graph.add_edge("calculate_tenure", "build_artifact")

    # Compile the graph — this makes it stateless and reusable
    return graph.compile()
