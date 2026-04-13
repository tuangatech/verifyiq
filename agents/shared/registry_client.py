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
        print("[Registry Client] AGENT_REGISTRY_URL not set — skipping registration")
        return None

    card_path = Path(agent_card_path)
    if not card_path.exists():
        print(f"[Registry Client] Agent card not found at {agent_card_path} — skipping registration")
        return None

    try:
        card = json.loads(card_path.read_text())
    except Exception as e:
        print(f"[Registry Client] Failed to read agent card: {e} — skipping registration")
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
            response = await client.post(f"{registry_url}/register", json=payload)
            if response.status_code == 201:
                result = response.json()
                url_hash = result.get("url_hash", "unknown")
                print(f"[Registry Client] Registered successfully — url_hash: {url_hash}")
                return url_hash
            else:
                print(f"[Registry Client] Registration failed: {response.status_code} {response.text}")
                return None
    except Exception as e:
        print(f"[Registry Client] Registration failed: {e}")
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
            response = await client.delete(f"{registry_url}/agents/{url_hash}")
            if response.status_code == 200:
                print(f"[Registry Client] Deregistered successfully — url_hash: {url_hash}")
            else:
                print(f"[Registry Client] Deregistration failed: {response.status_code} {response.text}")
    except Exception as e:
        print(f"[Registry Client] Deregistration failed: {e}")
