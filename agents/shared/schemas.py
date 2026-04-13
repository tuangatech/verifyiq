# agents/shared/schemas.py
"""
Shared Pydantic artifact models used across all agents.
These are the domain-level contracts — every agent that produces or consumes
an artifact references these models.
"""

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class EquifaxArtifact(BaseModel):
    """Output of the Equifax Credit Agent."""

    source: Literal["equifax"]
    subject_id: str
    credit_score: int = Field(..., ge=300, le=850)
    score_model: str = "FICO 8"
    open_accounts: int = Field(..., ge=0)
    credit_utilization_pct: float = Field(..., ge=0, le=100)
    derogatory_marks: int = Field(..., ge=0)
    hard_inquiries_12mo: int = Field(..., ge=0)
    oldest_account_years: float = Field(..., ge=0)
    data_as_of: date


class EmploymentArtifact(BaseModel):
    """Output of the Employment Verification Agent (Equifax Workforce Solutions / The Work Number)."""

    source: Literal["workforce_solutions"]
    subject_id: str
    currently_employed: bool
    employer_name: Optional[str] = None
    employment_start_date: Optional[date] = None
    tenure_years: Optional[float] = None
    employment_type: Optional[Literal["full_time", "part_time", "contract", "self_employed"]] = None
    verified_annual_income: Optional[int] = None
    income_currency: str = "USD"
    verification_confidence: Literal["high", "medium", "low"]
    data_as_of: date


class InternationalArtifact(BaseModel):
    """Output of the Experian International Agent."""

    source: Literal["experian_international"]
    subject_id: str
    data_availability: Literal["full", "partial", "unavailable"]
    reason: Optional[str] = None  # populated when data_availability is "unavailable"
    country_of_record: Optional[str] = None  # ISO 3166-1 alpha-2
    local_credit_score: Optional[int] = None
    us_equivalent_score: Optional[int] = None
    foreign_tradelines: Optional[int] = None
    country_risk_tier: Optional[Literal["low", "moderate", "high", "very_high"]] = None
    data_as_of: Optional[str] = None  # ISO date string


class VerificationDecision(BaseModel):
    """Output of the Risk Synthesis Agent — final decision sent to the caller."""

    decision: Literal["approve", "review", "decline"]
    confidence: Literal["high", "medium", "low"]
    risk_score: int = Field(..., ge=0, le=100)
    decision_factors: list[str]
    risk_flags: list[str]
    international_note: Optional[str] = None
    recommended_actions: list[str] = Field(default_factory=list)
    reasoning_summary: str
