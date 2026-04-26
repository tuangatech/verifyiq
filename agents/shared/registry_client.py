# agents/shared/registry_client.py
"""
Async helper functions for agent self-registration and deregistration with the Registry.
Kept in shared so no agent duplicates this logic.

Both functions read AGENT_REGISTRY_URL from the environment at call time, not at import time.
"""

import json
import os
from pathlib import Path

import httpx
import structlog

logger = structlog.get_logger()


def _auth_headers() -> dict[str, str]:
    """Return Authorization header if VERIFYIQ_AUTH_TOKEN is set."""
    token = os.environ.get("VERIFYIQ_AUTH_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


async def register_with_registry(agent_card_path: str = "agent_card.json") -> str | None:
    """
    Register this agent with the Registry service.

    Reads the Agent Card JSON from the given path, extracts name/skills/version/url,
    and POSTs a RegisterRequest to {AGENT_REGISTRY_URL}/register.

    Returns the url_hash on success, None on any failure.
    Must NOT raise — the agent should still start even if the Registry is briefly unavailable.
    """
    registry_url = os.environ.get("AGENT_REGISTRY_URL")
    if not registry_url:
        logger.warning("registration_skipped", reason="AGENT_REGISTRY_URL not set")
        return None

    card_path = Path(agent_card_path)
    if not card_path.exists():
        logger.warning("registration_skipped", reason=f"Agent card not found at {agent_card_path}")
        return None

    try:
        card = json.loads(card_path.read_text())
    except Exception as e:
        logger.warning("registration_skipped", reason=f"Failed to read agent card: {e}")
        return None

    # Extract skill IDs from the card's skills array
    skills = []
    for skill in card.get("skills", []):
        if isinstance(skill, dict) and "id" in skill:
            skills.append(skill["id"])
        elif isinstance(skill, str):
            skills.append(skill)

    payload = {
        "name": card.get("name", ""),
        "url": card.get("url", ""),
        "skills": skills,
        "version": card.get("version", "1.0.0"),
        "auth_schemes": card.get("auth_schemes", []),
        "capabilities": card.get("capabilities", {}),
        "agent_card_raw": card,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{registry_url}/register", json=payload, headers=_auth_headers(),
            )
            if response.status_code == 201:
                result = response.json()
                url_hash = result.get("url_hash", "unknown")
                logger.info("registration_success", url_hash=url_hash)
                return url_hash
            else:
                logger.warning("registration_failed", status=response.status_code, body=response.text)
                return None
    except Exception as e:
        logger.warning("registration_failed", error=str(e))
        return None


async def deregister_from_registry(url_hash: str | None) -> None:
    """
    Deregister this agent from the Registry on graceful shutdown.

    If url_hash is None or AGENT_REGISTRY_URL is unset, returns immediately (no-op).
    Must NOT raise — logging a warning is sufficient on failure.
    """
    if not url_hash:
        return

    registry_url = os.environ.get("AGENT_REGISTRY_URL")
    if not registry_url:
        return

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.delete(
                f"{registry_url}/agents/{url_hash}", headers=_auth_headers(),
            )
            if response.status_code == 200:
                logger.info("deregistration_success", url_hash=url_hash)
            else:
                logger.warning("deregistration_failed", status=response.status_code, body=response.text)
    except Exception as e:
        logger.warning("deregistration_failed", error=str(e))
