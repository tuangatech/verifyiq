# tests/test_phase8_auth.py
"""Phase 8 integration tests — bearer token auth enforcement.

Run:  pytest tests/test_phase8_auth.py -v

Requires VERIFYIQ_AUTH_TOKEN set in .env and passed to Docker Compose.
"""

import time
import uuid

import httpx
import pytest

from conftest import AUTH_HEADERS, AUTH_TOKEN

pytestmark = pytest.mark.asyncio

WRONG_HEADERS = {"Authorization": "Bearer wrong-token-value"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_request() -> dict:
    """Build a minimal VerificationRequest body."""
    return {
        "subject_name": "Auth Test",
        "subject_id": f"S-{uuid.uuid4().hex[:6]}",
        "use_case": "rental",
        "has_foreign_addr": False,
        "consent": True,
    }


def _a2a_task() -> dict:
    """Build a minimal A2ATask payload."""
    return {
        "task_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "skill": "credit_score",
        "input": {
            "subject_name": "Auth Test",
            "subject_id": "S-auth",
            "use_case": "rental",
            "has_foreign_addr": False,
            "consent": True,
        },
        "timeout_ms": 30000,
        "attempt": 1,
    }


# ---------------------------------------------------------------------------
# Orchestrator POST /verify auth
# ---------------------------------------------------------------------------

async def test_verify_rejects_missing_token(orchestrator_client: httpx.AsyncClient):
    """POST /verify with no Authorization header returns 401."""
    resp = await orchestrator_client.post("/verify", json=_verify_request())
    assert resp.status_code == 401


async def test_verify_rejects_invalid_token(orchestrator_client: httpx.AsyncClient):
    """POST /verify with wrong token returns 401."""
    resp = await orchestrator_client.post(
        "/verify", json=_verify_request(), headers=WRONG_HEADERS,
    )
    assert resp.status_code == 401


async def test_verify_accepts_valid_token(orchestrator_client: httpx.AsyncClient):
    """POST /verify with valid token returns 200."""
    resp = await orchestrator_client.post(
        "/verify", json=_verify_request(), headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data
    assert "correlation_id" in data


# ---------------------------------------------------------------------------
# Data agent POST /tasks/send auth
# ---------------------------------------------------------------------------

async def test_agent_tasks_send_rejects_missing_token(equifax_client: httpx.AsyncClient):
    """POST /tasks/send on Equifax without token returns 401."""
    resp = await equifax_client.post("/tasks/send", json=_a2a_task())
    assert resp.status_code == 401


async def test_agent_tasks_send_accepts_valid_token(equifax_client: httpx.AsyncClient):
    """POST /tasks/send on Equifax with valid token succeeds."""
    resp = await equifax_client.post(
        "/tasks/send", json=_a2a_task(), headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Registry POST /register auth
# ---------------------------------------------------------------------------

async def test_registry_register_rejects_missing_token(registry_client: httpx.AsyncClient):
    """POST /register without token returns 401."""
    payload = {
        "name": "Auth Test Agent",
        "url": "http://authtest:9999",
        "skills": ["test_skill"],
        "version": "1.0.0",
        "auth_schemes": [],
        "capabilities": {},
        "agent_card_raw": {},
    }
    resp = await registry_client.post("/register", json=payload)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET endpoints require no auth
# ---------------------------------------------------------------------------

async def test_get_endpoints_require_no_auth(orchestrator_client: httpx.AsyncClient):
    """GET /agents, /health, /.well-known/agent.json work without token."""
    agents_resp = await orchestrator_client.get("/agents")
    assert agents_resp.status_code == 200

    health_resp = await orchestrator_client.get("/health")
    assert health_resp.status_code == 200

    card_resp = await orchestrator_client.get("/.well-known/agent.json")
    assert card_resp.status_code == 200


# ---------------------------------------------------------------------------
# Full pipeline works with auth
# ---------------------------------------------------------------------------

async def test_full_pipeline_works_with_auth(orchestrator_client: httpx.AsyncClient):
    """Submit a verification with valid token — pipeline completes successfully."""
    body = _verify_request()
    resp = await orchestrator_client.post("/verify", json=body, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    deadline = time.time() + 60
    while time.time() < deadline:
        status_resp = await orchestrator_client.get(f"/verify/{task_id}")
        assert status_resp.status_code == 200
        status = status_resp.json()["status"]
        if status in ("completed", "failed"):
            break
        await __import__("asyncio").sleep(2)

    final = (await orchestrator_client.get(f"/verify/{task_id}")).json()
    assert final["status"] == "completed", f"Pipeline did not complete: {final}"
    assert final["decision"] in ("approve", "review", "decline")
