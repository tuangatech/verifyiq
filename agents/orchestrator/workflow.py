# agents/orchestrator/workflow.py
"""Use-case agent selection logic — determines which agents to invoke."""

from typing import TypeAlias

AgentEntry: TypeAlias = tuple[str, str, bool]  # (skill, agent_name, required)


def get_agent_plan(use_case: str, has_foreign_addr: bool) -> dict[str, list[AgentEntry]]:
    """Return which agents to invoke based on use_case and has_foreign_addr.

    Each entry in "parallel" is (skill, agent_name, required). required=True
    means the pipeline marks the overall request as failed if this agent fails,
    but synthesis still runs with the failure context. Agents not listed in
    "parallel" get a skipped AgentOutcome injected by the orchestrator.
    """
    equifax: AgentEntry = ("credit_score", "equifax", True)
    employment: AgentEntry = ("employment_status", "employment", True)
    intl_required: AgentEntry = ("international_credit_score", "intl", True)
    intl_optional: AgentEntry = ("international_credit_score", "intl", False)
    synthesis: AgentEntry = ("risk_synthesis", "synthesis", True)

    if use_case == "mortgage":
        parallel = [equifax, employment]
        if has_foreign_addr:
            parallel.append(intl_optional)
        return {"parallel": parallel, "sequential": [synthesis]}

    if use_case in ("rental", "auto"):
        return {
            "parallel": [equifax, employment],
            "sequential": [synthesis],
        }

    if use_case == "hire":
        parallel = [employment]
        if has_foreign_addr:
            parallel.append(intl_required)
        else:
            parallel.append(intl_optional)
        return {"parallel": parallel, "sequential": [synthesis]}

    # Unknown use case — default to full pipeline
    parallel = [equifax, employment]
    if has_foreign_addr:
        parallel.append(intl_optional)
    return {"parallel": parallel, "sequential": [synthesis]}
