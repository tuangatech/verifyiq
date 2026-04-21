"""LangGraph node functions for the Employment Verification workflow."""

import json
import os

import httpx

from agents.shared.schemas import EmploymentArtifact
from .graph_state import EmploymentGraphState
from .prompts import (
    current_employer_prompt,
    prior_employer_prompt,
    income_prompt,
)


async def lookup_current_employer(state: EmploymentGraphState) -> dict:
    """Node 1: Determine if currently employed and extract current employer info.

    If not currently employed, employer fields reflect prior employment or null.
    """
    subject_id = state["subject_id"]
    subject_name = state["subject_name"]
    use_case = state["use_case"]
    today = state["today"]

    system, user = current_employer_prompt(subject_id, subject_name, use_case)

    api_key = os.environ["OPENROUTER_API_KEY"]
    model = os.environ["OPENROUTER_AGENT_MODEL"]

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"},
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]

    data = json.loads(raw)
    return {
        "currently_employed": data["currently_employed"],
        "employer_name": data.get("employer_name"),
        "employment_start_date": data.get("employment_start_date"),
        "employment_type": data.get("employment_type"),
    }


async def lookup_prior_employer(state: EmploymentGraphState) -> dict:
    """Node 2: Find most recent prior employer (only runs if currently_employed == False).

    This fallback happens when the current employer lookup returns no active employment.
    """
    subject_id = state["subject_id"]
    subject_name = state["subject_name"]
    use_case = state["use_case"]

    system, user = prior_employer_prompt(subject_id, subject_name, use_case)

    api_key = os.environ["OPENROUTER_API_KEY"]
    model = os.environ["OPENROUTER_AGENT_MODEL"]

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"},
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]

    data = json.loads(raw)
    return {
        "prior_employer_name": data.get("employer_name"),
        "prior_employer_end_date": data.get("employment_end_date"),
    }


async def verify_income(state: EmploymentGraphState) -> dict:
    """Node 3: Generate income from payroll context.

    Runs after either branch of employer lookup completes.
    """
    subject_name = state["subject_name"]
    employer_name = state.get("employer_name") or state.get("prior_employer_name")
    employment_type = state.get("employment_type")
    use_case = state["use_case"]

    system, user = income_prompt(
        subject_name,
        employer_name or "unknown",
        employment_type or "unknown",
        use_case,
    )

    api_key = os.environ["OPENROUTER_API_KEY"]
    model = os.environ["OPENROUTER_AGENT_MODEL"]

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"},
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]

    data = json.loads(raw)
    return {
        "verified_annual_income": data.get("verified_annual_income", 0),
        "income_currency": data.get("income_currency", "USD"),
    }


def calculate_tenure(state: EmploymentGraphState) -> dict:
    """Node 4: Pure computation — compute tenure_years from dates.

    Sets verification_confidence based on tenure length and data quality.
    """
    employment_start_date = state.get("employment_start_date")
    currently_employed = state.get("currently_employed")
    prior_end_date = state.get("prior_employer_end_date")
    today = state["today"]

    tenure_years: Optional[float] = None
    confidence: Optional[str] = None

    # Compute tenure for current employment
    if employment_start_date and currently_employed:
        from datetime import datetime
        start = datetime.strptime(employment_start_date, "%Y-%m-%d")
        end = datetime.strptime(today, "%Y-%m-%d")
        tenure_years = (end - start).days / 365.25

    # If not currently employed but has prior employment, compute tenure until end date
    elif prior_end_date and currently_employed is False:
        from datetime import datetime
        start = datetime.strptime(employment_start_date or prior_employer_end_date, "%Y-%m-%d")
        end = datetime.strptime(prior_end_date or today, "%Y-%m-%d")
        tenure_years = (end - start).days / 365.25

    # Set confidence based on tenure and data completeness
    if tenure_years is not None:
        if tenure_years >= 3:
            confidence = "high"
        elif tenure_years >= 1:
            confidence = "medium"
        else:
            confidence = "low"
        # If no employer name, downgrade confidence
        if not state.get("employer_name"):
            confidence = "low"

    return {
        "tenure_years": round(tenure_years, 2) if tenure_years else None,
        "verification_confidence": confidence,
    }


def build_artifact(state: EmploymentGraphState) -> dict:
    """Node 5: Validate completeness and construct EmploymentArtifact.

    This is the final validation gate before returning results.
    """
    if state.get("error"):
        # Graph already routed to end due to error — just pass through
        return {}

    source = "workforce_solutions"
    subject_id = state["subject_id"]
    currently_employed = state.get("currently_employed", False)
    employer_name = state.get("employer_name") or state.get("prior_employer_name")
    employment_start_date = state.get("employment_start_date") or state.get("prior_employer_end_date")
    employment_type = state.get("employment_type")
    verified_annual_income = state.get("verified_annual_income")
    income_currency = state.get("income_currency", "USD")
    verification_confidence = state.get("verification_confidence", "medium")
    tenure_years = state.get("tenure_years")
    today = state["today"]

    # Validate required fields for a successful artifact
    if currently_employed is None:
        # Missing currently_employed — couldn't determine employment status
        from agents.shared.a2a_types import AgentError
        raise ValueError("Agent did not return currently_employed field")

    # Construct artifact — use computed values where applicable
    artifact = EmploymentArtifact(
        source=source,
        subject_id=subject_id,
        currently_employed=currently_employed,
        employer_name=employer_name,
        employment_start_date=employment_start_date,
        tenure_years=tenure_years,
        employment_type=employment_type,
        verified_annual_income=verified_annual_income,
        income_currency=income_currency,
        verification_confidence=verification_confidence,
        data_as_of=today,
    )

    return {"artifact": artifact.model_dump(mode="json")}
