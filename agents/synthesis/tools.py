# agents/synthesis/tools.py
"""Plain Python functions that call OpenRouter to generate VerificationDecision."""

import json
import os

import httpx

from agents.shared.schemas import VerificationDecision
from .prompts import SYSTEM_PROMPT, build_synthesis_prompt


def parse_outcome_bundle(task_input: dict) -> tuple[list[dict], str]:
    """Extract outcomes list and use_case from task input."""
    if "outcomes" not in task_input:
        raise ValueError("Missing required field 'outcomes' in task input")
    if "use_case" not in task_input:
        raise ValueError("Missing required field 'use_case' in task input")
    return task_input["outcomes"], task_input["use_case"]


async def call_llm_decision(outcomes: list[dict], use_case: str) -> str:
    """POST to OpenRouter with synthesis model and return raw JSON content string."""
    api_key = os.environ["OPENROUTER_API_KEY"]
    model = os.environ["OPENROUTER_SYNTHESIS_MODEL"]

    user_prompt = build_synthesis_prompt(outcomes, use_case)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def validate_decision_artifact(raw: str) -> VerificationDecision:
    """Parse raw JSON string into a validated VerificationDecision."""
    data = json.loads(raw)
    return VerificationDecision(**data)


async def retry_with_correction(raw: str, error: str, outcomes: list[dict], use_case: str) -> str:
    """Retry LLM call with the validation error appended to the prompt."""
    api_key = os.environ["OPENROUTER_API_KEY"]
    model = os.environ["OPENROUTER_SYNTHESIS_MODEL"]

    user_prompt = build_synthesis_prompt(outcomes, use_case)
    corrected_prompt = (
        f"{user_prompt}\n\n"
        f"Your previous output failed validation: {error}\n"
        f"Correct and return only valid JSON."
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": corrected_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
