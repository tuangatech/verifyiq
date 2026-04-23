"""Per-node prompt builders for the Employment Verification LangGraph workflow."""

from typing import Tuple

# Shared system prompt — avoids repetition across all three LLM nodes
BASE_SYSTEM_PROMPT = """You are a simulated Equifax Workforce Solutions / The Work Number employment verification API.
Respond with ONLY a valid JSON object matching the specified schema — no explanation, no markdown, no extra text.
"""


def current_employer_prompt(subject_id: str, subject_name: str, use_case: str) -> Tuple[str, str]:
    """Node 1: Determines if currently employed and extracts current employer info."""
    system = BASE_SYSTEM_PROMPT
    user = f"""Generate employment verification data for:
- Subject ID: {subject_id}
- Subject Name: {subject_name}
- Use Case: {use_case}
- Data As Of: {get_today()}

Determine if the subject is currently employed. Return ONLY this JSON schema:
{{
  "currently_employed": true|false,
  "employer_name": "<string or null>",
  "employment_start_date": "<YYYY-MM-DD or null>",
  "employment_type": "<full_time | part_time | contract | self_employed or null>"
}}

Guidelines:
- Most subjects (70-80%) are currently employed
- Mortgage applicants tend to have stable full-time employment
- Auto/rental applicants have more varied employment situations
- Hire/background checks often show the current employer
- If currently_employed is false, set employer fields to null"""

    return system, user


def prior_employer_prompt(subject_id: str, subject_name: str, use_case: str) -> Tuple[str, str]:
    """Node 2: Finds most recent prior employer (only runs if currently_employed == False)."""
    system = BASE_SYSTEM_PROMPT
    user = f"""Generate employment verification data for:
- Subject ID: {subject_id}
- Subject Name: {subject_name}
- Use Case: {use_case}
- Data As Of: {get_today()}

Since the subject is not currently employed, find their most recent prior employer. Return ONLY this JSON schema:
{{
  "employer_name": "<string>",
  "employment_start_date": "<YYYY-MM-DD>",
  "employment_end_date": "<YYYY-MM-DD>"
}}

Guidelines:
- Prior employer should reflect recent employment history
- Employment end date should be within the last 5 years
- Employment start date should be before the end date (typically 1-10 years of tenure)
- Use realistic dates based on the subject's employment pattern"""

    return system, user


def income_prompt(subject_name: str, employer_name: str, employment_type: str, use_case: str) -> Tuple[str, str]:
    """Node 3: Generates income from payroll context."""
    system = BASE_SYSTEM_PROMPT
    user = f"""Generate income data for:
- Subject Name: {subject_name}
- Employer Name: {employer_name}
- Employment Type: {employment_type}
- Use Case: {use_case}
- Data As Of: {get_today()}

Return ONLY this JSON schema:
{{
  "verified_annual_income": <integer>,
  "income_currency": "USD"
}}

Guidelines for realistic income ranges by employment_type:
- full_time: $35,000 - $250,000
- part_time: $15,000 - $60,000
- contract: $40,000 - $300,000
- self_employed: varies widely, $20,000 - $500,000

Select income realistic for the employment type and use_case:
- Mortgage applicants: stable income, middle-to-upper range
- Rental applicants: moderate income
- Auto/hire applicants: varied income levels"""

    return system, user


def get_today() -> str:
    """Return today's date in YYYY-MM-DD format."""
    from datetime import date
    return date.today().isoformat()
