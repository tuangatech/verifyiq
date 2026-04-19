# agents/equifax/tools.py
"""Plain Python functions that call OpenRouter to generate EquifaxArtifact."""

import json
import os
from datetime import date

import httpx

from agents.shared.schemas import EquifaxArtifact
from .prompts import SYSTEM_PROMPT, build_user_prompt


def build_persona_seed(task_input: dict) -> dict:
    """Extract subject seed data from task input."""
    return {
        "subject_id": task_input.get("subject_id", "unknown"),
        "subject_name": task_input.get("subject_name", "Unknown Subject"),
        "use_case": task_input.get("use_case", "mortgage"),
        "today": date.today().isoformat(),
    }


async def call_llm_credit_profile(seed: dict) -> str:
    """POST to OpenRouter and return raw JSON content string."""
    api_key = os.environ["OPENROUTER_API_KEY"]
    model = os.environ["OPENROUTER_AGENT_MODEL"]

    user_prompt = build_user_prompt(
        seed["subject_id"], seed["subject_name"], seed["use_case"], seed["today"]
    )

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
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def validate_credit_artifact(raw: str) -> EquifaxArtifact:
    """Parse raw JSON string into a validated EquifaxArtifact."""
    data = json.loads(raw)
    return EquifaxArtifact(**data)


async def retry_with_correction(raw: str, error: str, seed: dict) -> str:
    """Retry LLM call with the validation error appended to the prompt."""
    api_key = os.environ["OPENROUTER_API_KEY"]
    model = os.environ["OPENROUTER_AGENT_MODEL"]

    user_prompt = build_user_prompt(
        seed["subject_id"], seed["subject_name"], seed["use_case"], seed["today"]
    )
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
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
