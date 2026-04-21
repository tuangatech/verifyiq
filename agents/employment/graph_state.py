"""Graph state for the Employment Verification LangGraph workflow."""

from datetime import date
from typing import TypedDict, Optional


class EmploymentGraphState(TypedDict, total=False):
    """State that flows through all graph nodes.

    Nodes read what they need and write their outputs progressively.
    Not all fields exist after every node — use total=False for flexibility.
    """

    # Input (set once at entry)
    subject_id: str
    subject_name: str
    use_case: str
    today: str

    # Employer lookup output
    currently_employed: Optional[bool]
    employer_name: Optional[str]
    employment_start_date: Optional[str]
    employment_type: Optional[str]

    # Prior employer fallback
    prior_employer_name: Optional[str]
    prior_employer_end_date: Optional[str]

    # Income
    verified_annual_income: Optional[int]
    income_currency: str

    # Computed
    tenure_years: Optional[float]
    verification_confidence: Optional[str]

    # Error (if any node fails, short-circuits to end)
    error: Optional[str]
