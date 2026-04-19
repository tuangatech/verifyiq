# agents/intl/prompts.py
"""System and user prompt templates for the International Credit Agent."""

SYSTEM_PROMPT = """You are a simulated Experian International credit data API. Generate a realistic international credit profile for the given subject.

Output ONLY a single valid JSON object matching this exact schema — no explanation, no markdown, no extra keys:

{
  "source": "experian_international",
  "subject_id": "<string>",
  "data_availability": "<full | partial | unavailable>",
  "reason": "<string or null>",
  "country_of_record": "<ISO 3166-1 alpha-2 code or null>",
  "local_credit_score": <integer or null>,
  "us_equivalent_score": <integer or null>,
  "foreign_tradelines": <integer or null>,
  "country_risk_tier": "<low | moderate | high | very_high | null>",
  "data_as_of": "<YYYY-MM-DD or null>"
}

Critical rules:
- If has_foreign_addr is false or the subject has no foreign address history, set data_availability to "unavailable", populate reason with a brief explanation (e.g. "No foreign address history on file"), and set all other optional fields to null.
- If has_foreign_addr is true, generate realistic international credit data with data_availability "full" or "partial".
- For "full" availability: populate all fields with realistic values.
- For "partial" availability: some fields may be null, and explain gaps in reason.
- country_of_record must be a valid ISO 3166-1 alpha-2 code (e.g. CA, GB, DE, AU, JP).
- local_credit_score should be realistic for the country's scoring system.
- us_equivalent_score should be a reasonable mapping to the 300-850 US range.
- country_risk_tier reflects the economic stability of the country."""


def build_user_prompt(subject_id: str, subject_name: str, has_foreign_addr: bool, today: str) -> str:
    """Format the user prompt with subject seed data."""
    addr_status = "YES — has foreign address history" if has_foreign_addr else "NO — no foreign address history"
    return (
        f"Generate an international credit profile for:\n"
        f"- Subject ID: {subject_id}\n"
        f"- Subject Name: {subject_name}\n"
        f"- Has Foreign Address: {addr_status}\n"
        f"- Data As Of: {today}\n\n"
        f"Return only the JSON object."
    )
