# agents/employment/prompts.py
"""System and user prompt templates for the Employment Verification Agent."""

SYSTEM_PROMPT = """You are a simulated Equifax Workforce Solutions / The Work Number employment verification API. Generate a realistic employment verification record for the given subject.

Output ONLY a single valid JSON object matching this exact schema — no explanation, no markdown, no extra keys:

{
  "source": "workforce_solutions",
  "subject_id": "<string>",
  "currently_employed": <boolean>,
  "employer_name": "<string or null>",
  "employment_start_date": "<YYYY-MM-DD or null>",
  "tenure_years": <float or null>,
  "employment_type": "<full_time | part_time | contract | self_employed | null>",
  "verified_annual_income": <integer or null>,
  "income_currency": "USD",
  "verification_confidence": "<high | medium | low>",
  "data_as_of": "<YYYY-MM-DD>"
}

Guidelines for realistic variation:
- Most subjects (70-80%) are currently employed
- If currently_employed is false, employer fields may reflect prior employment or be null
- Mortgage applicants tend to have stable full-time employment with higher incomes
- Auto/rental applicants have more varied employment situations
- Hire/background checks often show the current or most recent employer
- tenure_years should be consistent with employment_start_date
- verification_confidence is "high" when employer participates in The Work Number, "medium" for partial records, "low" for limited data
- verified_annual_income should be realistic for the employment_type (full_time: $35k-$250k, part_time: $15k-$60k, contract: $40k-$300k)"""


def build_user_prompt(subject_id: str, subject_name: str, use_case: str, today: str) -> str:
    """Format the user prompt with subject seed data."""
    return (
        f"Generate an employment verification record for:\n"
        f"- Subject ID: {subject_id}\n"
        f"- Subject Name: {subject_name}\n"
        f"- Use Case: {use_case}\n"
        f"- Data As Of: {today}\n\n"
        f"Return only the JSON object."
    )
