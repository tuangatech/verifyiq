# tests/test_phase4_llm.py
"""Phase 4 integration tests — LLM artifact generation with live OpenRouter calls."""

import uuid

import pytest

from agents.shared.schemas import (
    EquifaxArtifact,
    EmploymentArtifact,
    InternationalArtifact,
    VerificationDecision,
)


def _task(skill: str, input_dict: dict) -> dict:
    """Build an A2A task payload."""
    return {
        "task_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "skill": skill,
        "input": input_dict,
        "timeout_ms": 30000,
        "attempt": 1,
    }


@pytest.mark.asyncio
async def test_equifax_produces_valid_artifact(equifax_client):
    """Equifax returns a schema-valid credit artifact from LLM."""
    payload = _task("credit_score", {
        "subject_id": "S001", "subject_name": "Jane Doe",
        "use_case": "mortgage", "has_foreign_addr": True,
    })
    resp = await equifax_client.post("/tasks/send", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    artifact = EquifaxArtifact(**data["artifact"])
    assert 300 <= artifact.credit_score <= 850
    assert artifact.source == "equifax"


@pytest.mark.asyncio
async def test_equifax_output_varies(equifax_client):
    """Calling Equifax with different subjects produces varied credit scores."""
    scores = []
    for name in ["Alice Smith", "Bob Jones", "Carlos Rivera"]:
        payload = _task("credit_score", {
            "subject_id": f"S-{name.replace(' ', '')}", "subject_name": name,
            "use_case": "mortgage", "has_foreign_addr": False,
        })
        resp = await equifax_client.post("/tasks/send", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        scores.append(data["artifact"]["credit_score"])
    assert len(set(scores)) > 1, f"All scores identical: {scores}"


@pytest.mark.asyncio
async def test_employment_produces_valid_artifact(employment_client):
    """Employment returns a schema-valid employment artifact from LLM."""
    payload = _task("employment_status", {
        "subject_id": "S002", "subject_name": "John Smith",
        "use_case": "mortgage", "has_foreign_addr": False,
    })
    resp = await employment_client.post("/tasks/send", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    artifact = EmploymentArtifact(**data["artifact"])
    assert artifact.verification_confidence in ("high", "medium", "low")
    assert artifact.source == "workforce_solutions"


@pytest.mark.asyncio
async def test_intl_full_when_foreign_addr_true(intl_client):
    """International with has_foreign_addr=true returns full or partial data."""
    payload = _task("international_credit_score", {
        "subject_id": "S003", "subject_name": "Maria Garcia",
        "use_case": "mortgage", "has_foreign_addr": True,
    })
    resp = await intl_client.post("/tasks/send", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    artifact = InternationalArtifact(**data["artifact"])
    assert artifact.data_availability in ("full", "partial")


@pytest.mark.asyncio
async def test_intl_unavailable_when_no_foreign_addr(intl_client):
    """International with has_foreign_addr=false returns unavailable."""
    payload = _task("international_credit_score", {
        "subject_id": "S004", "subject_name": "Tom Brown",
        "use_case": "rental", "has_foreign_addr": False,
    })
    resp = await intl_client.post("/tasks/send", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    artifact = InternationalArtifact(**data["artifact"])
    assert artifact.data_availability == "unavailable"
    assert artifact.reason is not None


@pytest.mark.asyncio
async def test_synthesis_produces_valid_decision(synthesis_client):
    """Synthesis with a complete outcome bundle produces a valid decision."""
    outcomes = [
        {
            "agent_name": "equifax", "skill": "credit_score", "status": "completed",
            "artifact": {
                "source": "equifax", "subject_id": "S005", "credit_score": 740,
                "score_model": "FICO 8", "open_accounts": 8,
                "credit_utilization_pct": 25.0, "derogatory_marks": 0,
                "hard_inquiries_12mo": 1, "oldest_account_years": 12.0,
                "data_as_of": "2026-04-18",
            },
            "error": None,
        },
        {
            "agent_name": "employment", "skill": "employment_status", "status": "completed",
            "artifact": {
                "source": "workforce_solutions", "subject_id": "S005",
                "currently_employed": True, "employer_name": "Acme Corp",
                "employment_start_date": "2019-06-01", "tenure_years": 6.8,
                "employment_type": "full_time", "verified_annual_income": 95000,
                "income_currency": "USD", "verification_confidence": "high",
                "data_as_of": "2026-04-18",
            },
            "error": None,
        },
        {
            "agent_name": "intl", "skill": "international_credit_score", "status": "completed",
            "artifact": {
                "source": "experian_international", "subject_id": "S005",
                "data_availability": "full", "reason": None,
                "country_of_record": "GB", "local_credit_score": 780,
                "us_equivalent_score": 720, "foreign_tradelines": 4,
                "country_risk_tier": "low", "data_as_of": "2026-04-18",
            },
            "error": None,
        },
    ]
    payload = _task("risk_synthesis", {"outcomes": outcomes, "use_case": "mortgage"})
    resp = await synthesis_client.post("/tasks/send", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    artifact = VerificationDecision(**data["artifact"])
    assert artifact.decision in ("approve", "review", "decline")
    assert 0 <= artifact.risk_score <= 100


@pytest.mark.asyncio
async def test_synthesis_declines_on_weak_profile(synthesis_client):
    """Synthesis with a weak profile should not approve."""
    outcomes = [
        {
            "agent_name": "equifax", "skill": "credit_score", "status": "completed",
            "artifact": {
                "source": "equifax", "subject_id": "S006", "credit_score": 440,
                "score_model": "FICO 8", "open_accounts": 14,
                "credit_utilization_pct": 95.0, "derogatory_marks": 5,
                "hard_inquiries_12mo": 8, "oldest_account_years": 1.5,
                "data_as_of": "2026-04-18",
            },
            "error": None,
        },
        {
            "agent_name": "employment", "skill": "employment_status", "status": "completed",
            "artifact": {
                "source": "workforce_solutions", "subject_id": "S006",
                "currently_employed": False, "employer_name": None,
                "employment_start_date": None, "tenure_years": None,
                "employment_type": None, "verified_annual_income": None,
                "income_currency": "USD", "verification_confidence": "low",
                "data_as_of": "2026-04-18",
            },
            "error": None,
        },
        {
            "agent_name": "intl", "skill": "international_credit_score", "status": "skipped",
            "artifact": None, "error": None,
        },
    ]
    payload = _task("risk_synthesis", {"outcomes": outcomes, "use_case": "mortgage"})
    resp = await synthesis_client.post("/tasks/send", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    artifact = VerificationDecision(**data["artifact"])
    assert artifact.decision != "approve"


@pytest.mark.asyncio
async def test_synthesis_handles_timed_out_outcome(synthesis_client):
    """Synthesis still produces a decision when one agent timed out."""
    outcomes = [
        {
            "agent_name": "equifax", "skill": "credit_score", "status": "completed",
            "artifact": {
                "source": "equifax", "subject_id": "S007", "credit_score": 700,
                "score_model": "FICO 8", "open_accounts": 6,
                "credit_utilization_pct": 35.0, "derogatory_marks": 1,
                "hard_inquiries_12mo": 3, "oldest_account_years": 5.0,
                "data_as_of": "2026-04-18",
            },
            "error": None,
        },
        {
            "agent_name": "employment", "skill": "employment_status", "status": "completed",
            "artifact": {
                "source": "workforce_solutions", "subject_id": "S007",
                "currently_employed": True, "employer_name": "Tech Startup LLC",
                "employment_start_date": "2022-03-15", "tenure_years": 4.1,
                "employment_type": "full_time", "verified_annual_income": 72000,
                "income_currency": "USD", "verification_confidence": "medium",
                "data_as_of": "2026-04-18",
            },
            "error": None,
        },
        {
            "agent_name": "intl", "skill": "international_credit_score", "status": "timed_out",
            "artifact": None,
            "error": {"code": "TIMEOUT", "message": "Agent did not respond within timeout", "retryable": True},
        },
    ]
    payload = _task("risk_synthesis", {"outcomes": outcomes, "use_case": "mortgage"})
    resp = await synthesis_client.post("/tasks/send", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    artifact = VerificationDecision(**data["artifact"])
    assert artifact.confidence in ("medium", "low")
