# agents/orchestrator/workflow.py
"""Use-case agent selection logic — determines which agents to invoke."""

from typing import TypeAlias

AgentEntry: TypeAlias = tuple[str, str, bool]  # (skill, agent_name, required)

# Every data-layer skill the system knows about. Skills not selected by a
# use-case plan are marked "skipped" so Risk Synthesis has a complete picture.
ALL_DATA_SKILLS: set[str] = {
    "credit_score",
    "employment_status",
    "international_credit_score",
}


def get_agent_plan(
    use_case: str, has_foreign_addr: bool
) -> dict[str, list[AgentEntry] | list[str]]:
    """Return which agents to invoke and which skills to skip.

    Each entry in "parallel" is (skill, agent_name, required). required=True
    means the pipeline marks the overall request as failed if this agent fails,
    but synthesis still runs with the failure context.

    "skipped_skills" lists skills not needed for this use case. Skipping is
    skill-level, not agent-level — if three agents all serve "credit_score",
    none of them are invoked and Synthesis sees one "credit_score: skipped".
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
        return _with_skipped(parallel, [synthesis])

    if use_case in ("rental", "auto"):
        return _with_skipped([equifax, employment], [synthesis])

    if use_case == "hire":
        parallel = [employment]
        if has_foreign_addr:
            parallel.append(intl_required)
        else:
            parallel.append(intl_optional)
        return _with_skipped(parallel, [synthesis])

    # Unknown use case — default to full pipeline
    parallel = [equifax, employment]
    if has_foreign_addr:
        parallel.append(intl_optional)
    return _with_skipped(parallel, [synthesis])


def _with_skipped(
    parallel: list[AgentEntry], sequential: list[AgentEntry]
) -> dict[str, list]:
    """Attach skipped_skills by diffing the parallel plan against ALL_DATA_SKILLS."""
    invoked = {skill for skill, _, _ in parallel}
    skipped = sorted(ALL_DATA_SKILLS - invoked)
    return {"parallel": parallel, "sequential": sequential, "skipped_skills": skipped}
