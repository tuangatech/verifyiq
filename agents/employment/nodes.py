"""LangGraph node functions for the Employment Verification workflow."""

import json
import os
from datetime import datetime
from typing import Optional

import httpx

from agents.shared.schemas import EmploymentArtifact
from .graph_state import EmploymentGraphState
from .prompts import (
    current_employer_prompt,
    prior_employer_prompt,
    income_prompt,
)


async def lookup_current_employer(state: EmploymentGraphState) -> dict:
    """Node 1: Determine if currently employed and extract current employer info."""
    try:
        subject_id = state["subject_id"]
        subject_name = state["subject_name"]
        use_case = state["use_case"]

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
    except Exception as exc:
        return {"error": f"lookup_current_employer failed: {exc}"}


async def lookup_prior_employer(state: EmploymentGraphState) -> dict:
    """Node 2: Find most recent prior employer (only runs if currently_employed == False)."""
    try:
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
            "prior_employer_start_date": data.get("employment_start_date"),
            "prior_employer_end_date": data.get("employment_end_date"),
        }
    except Exception as exc:
        return {"error": f"lookup_prior_employer failed: {exc}"}


async def verify_income(state: EmploymentGraphState) -> dict:
    """Node 3: Generate income from payroll context."""
    if state.get("error"):
        return {}
    try:
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
    except Exception as exc:
        return {"error": f"verify_income failed: {exc}"}


def calculate_tenure(state: EmploymentGraphState) -> dict:
    """Node 4: Pure computation — compute tenure_years from dates."""
    if state.get("error"):
        return {}
    employment_start_date = state.get("employment_start_date")
    currently_employed = state.get("currently_employed")
    prior_start_date = state.get("prior_employer_start_date")
    prior_end_date = state.get("prior_employer_end_date")
    today = state["today"]

    tenure_years: Optional[float] = None
    confidence: Optional[str] = None

    # Compute tenure for current employment
    if employment_start_date and currently_employed:
        start = datetime.strptime(employment_start_date, "%Y-%m-%d")
        end = datetime.strptime(today, "%Y-%m-%d")
        tenure_years = (end - start).days / 365.25

    # If not currently employed, compute tenure from prior employer dates
    elif currently_employed is False and prior_start_date and prior_end_date:
        start = datetime.strptime(prior_start_date, "%Y-%m-%d")
        end = datetime.strptime(prior_end_date, "%Y-%m-%d")
        tenure_years = (end - start).days / 365.25

    # Set confidence based on tenure and data completeness
    if tenure_years is not None and tenure_years > 0:
        if tenure_years >= 3:
            confidence = "high"
        elif tenure_years >= 1:
            confidence = "medium"
        else:
            confidence = "low"
    else:
        confidence = "low"

    if not state.get("employer_name") and not state.get("prior_employer_name"):
        confidence = "low"

    return {
        "tenure_years": round(tenure_years, 2) if tenure_years and tenure_years > 0 else None,
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
    employment_start_date = state.get("employment_start_date") or state.get("prior_employer_start_date")
    employment_type = state.get("employment_type")
    verified_annual_income = state.get("verified_annual_income")
    income_currency = state.get("income_currency") or "USD"
    verification_confidence = state.get("verification_confidence") or "low"
    tenure_years = state.get("tenure_years")
    today = state["today"]

    if currently_employed is None:
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
