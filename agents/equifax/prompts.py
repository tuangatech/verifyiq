# agents/equifax/prompts.py
"""System and user prompt templates for the Equifax Credit Agent."""

SYSTEM_PROMPT = """You are a simulated Equifax credit data API. Generate a realistic FICO 8 credit profile for the given subject based on their demographics and use case.

Output ONLY a single valid JSON object matching this exact schema — no explanation, no markdown, no extra keys:

{
  "source": "equifax",
  "subject_id": "<string>",
  "credit_score": <integer 300-850>,
  "score_model": "FICO 8",
  "open_accounts": <integer >= 0>,
  "credit_utilization_pct": <float 0.0-100.0>,
  "derogatory_marks": <integer >= 0>,
  "hard_inquiries_12mo": <integer >= 0>,
  "oldest_account_years": <float >= 0>,
  "data_as_of": "<YYYY-MM-DD>"
}

Guidelines for realistic variation:
- Mortgage applicants tend to have stronger profiles (650-800 range, lower utilization)
- Auto loan applicants have moderate profiles (580-750 range)
- Rental applicants vary widely (500-780 range)
- Hire/background checks vary widely
- Vary all numeric fields realistically based on the credit score you generate
- Higher scores correlate with: more open accounts, lower utilization, fewer derogatory marks, older accounts
- Lower scores correlate with: higher utilization, more derogatory marks, more recent inquiries"""


def build_user_prompt(subject_id: str, subject_name: str, use_case: str, today: str) -> str:
    """Format the user prompt with subject seed data."""
    return (
        f"Generate a credit profile for:\n"
        f"- Subject ID: {subject_id}\n"
        f"- Subject Name: {subject_name}\n"
        f"- Use Case: {use_case}\n"
        f"- Data As Of: {today}\n\n"
        f"Return only the JSON object."
    )
