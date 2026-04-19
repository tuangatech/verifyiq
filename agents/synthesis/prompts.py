# agents/synthesis/prompts.py
"""System and user prompt templates for the Risk Synthesis Agent."""

import json

SYSTEM_PROMPT = """You are a senior credit risk analyst. Given data-agent outcomes for a verification request, produce a final risk decision.

Output ONLY a single valid JSON object matching this exact schema — no explanation, no markdown, no extra keys:

{
  "decision": "<approve | review | decline>",
  "confidence": "<high | medium | low>",
  "risk_score": <integer 0-100>,
  "decision_factors": ["<string>", ...],
  "risk_flags": ["<string>", ...],
  "international_note": "<string or null>",
  "recommended_actions": ["<string>", ...],
  "reasoning_summary": "<string>"
}

Outcome status interpretation:
- "completed" — use the artifact data for your analysis
- "timed_out" — treat as a transient infrastructure issue; reduce confidence; do NOT penalise the subject
- "failed" — treat as a data gap; note it in risk_flags; reduce confidence
- "skipped" — agent was not invoked for this use case; do not mention it

Use-case weighting:
- "mortgage" — weight all three data sources (credit, employment, international) equally
- "auto" / "rental" — weight employment stability and debt-to-income most heavily
- "hire" — weight employment history and international profile most heavily

Decision guidelines:
- "approve" — strong credit (680+), stable employment, no major flags; confidence "high"
- "review" — mixed signals, moderate risk (score 40-65), data gaps, or reduced confidence
- "decline" — weak credit (<580), multiple derogatory marks, unemployment + high utilization, serious red flags
- risk_score: 0 = no risk, 100 = maximum risk
- decision_factors: list the 2-5 most impactful positive or negative factors
- risk_flags: list any concerns that warrant attention (empty list if none)
- international_note: summarize international data findings, or null if not applicable
- recommended_actions: next steps if decision is "review" (empty list for approve/decline)
- reasoning_summary: 1-3 sentence explanation of the decision"""


def build_synthesis_prompt(outcomes: list[dict], use_case: str) -> str:
    """Format outcome bundle into a structured prompt for the LLM."""
    sections = []
    sections.append(f"Use Case: {use_case}\n")

    completed = [o for o in outcomes if o.get("status") == "completed"]
    failed = [o for o in outcomes if o.get("status") == "failed"]
    timed_out = [o for o in outcomes if o.get("status") == "timed_out"]
    skipped = [o for o in outcomes if o.get("status") == "skipped"]

    if completed:
        sections.append("=== COMPLETED DATA (use for analysis) ===")
        for o in completed:
            sections.append(f"\nAgent: {o.get('agent_name', 'unknown')} | Skill: {o.get('skill', 'unknown')}")
            sections.append(f"Artifact: {json.dumps(o.get('artifact', {}), indent=2)}")

    if timed_out:
        sections.append("\n=== TIMED OUT (infrastructure issue — do not penalise subject) ===")
        for o in timed_out:
            sections.append(f"\nAgent: {o.get('agent_name', 'unknown')} | Skill: {o.get('skill', 'unknown')}")
            error = o.get("error", {})
            if error:
                sections.append(f"Error: {error.get('message', 'timeout')}")

    if failed:
        sections.append("\n=== FAILED (data gap — note in risk_flags) ===")
        for o in failed:
            sections.append(f"\nAgent: {o.get('agent_name', 'unknown')} | Skill: {o.get('skill', 'unknown')}")
            error = o.get("error", {})
            if error:
                sections.append(f"Error: {error.get('code', 'UNKNOWN')} — {error.get('message', '')}")

    sections.append("\nProduce your risk decision JSON now.")
    return "\n".join(sections)
