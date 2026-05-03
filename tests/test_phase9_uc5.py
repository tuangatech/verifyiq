# tests/test_phase9_uc5.py
"""Phase 9 integration tests — A2A callee endpoints + Mortgage Platform (UC-5).

Run:  pytest tests/test_phase9_uc5.py -v

Orchestrator tests (1-6): docker compose up -d
Mortgage Platform test (7): docker compose --profile uc5 up -d
"""

import asyncio
import json
import time
import uuid

import httpx
import pytest

from conftest import AUTH_HEADERS

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _a2a_task(skill: str = "verify_subject") -> dict:
    """Build an A2A task payload for the Orchestrator callee endpoint."""
    return {
        "task_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "skill": skill,
        "input": {
            "subject_name": "David Kim",
            "subject_id": "SIM-KR-2018",
            "use_case": "mortgage",
            "has_foreign_addr": True,
            "consent": True,
        },
        "timeout_ms": 60000,
        "attempt": 1,
    }


async def _poll_a2a_task(client: httpx.AsyncClient, task_id: str, timeout: float = 90.0) -> dict:
    """Poll GET /tasks/{task_id} until terminal status."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = await client.get(f"/tasks/{task_id}")
        assert resp.status_code == 200
        result = resp.json()
        if result["status"] in ("completed", "failed"):
            return result
        await asyncio.sleep(2)
    pytest.fail(f"Task {task_id} did not reach terminal status within {timeout}s")


# ---------------------------------------------------------------------------
# 1. Agent card advertises bearer auth
# ---------------------------------------------------------------------------

async def test_orchestrator_agent_card_has_bearer_auth(orchestrator_client: httpx.AsyncClient):
    """Agent card includes auth_schemes: ['bearer'] and verify_subject skill."""
    resp = await orchestrator_client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    card = resp.json()
    assert "bearer" in card["auth_schemes"]
    skill_ids = [s["id"] for s in card["skills"]]
    assert "verify_subject" in skill_ids
    assert card["capabilities"]["streaming"] is True


# ---------------------------------------------------------------------------
# 2. POST /tasks/send returns submitted immediately
# ---------------------------------------------------------------------------

async def test_tasks_send_returns_submitted(orchestrator_client: httpx.AsyncClient):
    """POST /tasks/send returns task_id and status='submitted' without blocking."""
    task = _a2a_task()
    start = time.time()
    resp = await orchestrator_client.post("/tasks/send", json=task, headers=AUTH_HEADERS)
    elapsed = time.time() - start
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == task["task_id"]
    assert data["status"] == "submitted"
    assert elapsed < 3.0, "POST /tasks/send should return immediately"


# ---------------------------------------------------------------------------
# 3. POST /tasks/send rejects missing auth
# ---------------------------------------------------------------------------

async def test_tasks_send_rejects_missing_auth(orchestrator_client: httpx.AsyncClient):
    """POST /tasks/send without Authorization header returns 401."""
    resp = await orchestrator_client.post("/tasks/send", json=_a2a_task())
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 4. POST /tasks/send rejects wrong skill
# ---------------------------------------------------------------------------

async def test_tasks_send_rejects_wrong_skill(orchestrator_client: httpx.AsyncClient):
    """POST /tasks/send with unsupported skill returns 422."""
    task = _a2a_task(skill="nonexistent_skill")
    resp = await orchestrator_client.post("/tasks/send", json=task, headers=AUTH_HEADERS)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 5. Full A2A round-trip: send → poll → completed with artifact
# ---------------------------------------------------------------------------

async def test_tasks_get_returns_completed(orchestrator_client: httpx.AsyncClient):
    """Submit via A2A, poll until completed, verify artifact has a decision."""
    task = _a2a_task()
    resp = await orchestrator_client.post("/tasks/send", json=task, headers=AUTH_HEADERS)
    assert resp.status_code == 200

    result = await _poll_a2a_task(orchestrator_client, task["task_id"])
    assert result["status"] == "completed"
    assert result["artifact"] is not None
    assert result["artifact"]["decision"] in ("approve", "review", "decline")
    assert "internal_task_id" in result


# ---------------------------------------------------------------------------
# 6. SSE stream delivers events for A2A-submitted task
# ---------------------------------------------------------------------------

async def test_tasks_stream_delivers_events(orchestrator_client: httpx.AsyncClient):
    """Submit via A2A, connect to SSE stream, verify events are delivered."""
    task = _a2a_task()
    resp = await orchestrator_client.post("/tasks/send", json=task, headers=AUTH_HEADERS)
    assert resp.status_code == 200

    # Wait briefly for pipeline to start, then poll until done
    result = await _poll_a2a_task(orchestrator_client, task["task_id"])
    assert result["status"] == "completed"

    # Now replay SSE events (pipeline is done, so this is a DB replay)
    events = []
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=30.0) as stream_client:
        async with stream_client.stream("GET", f"/tasks/{task['task_id']}/stream") as stream_resp:
            async for line in stream_resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload_str = line[5:].strip()
                if not payload_str:
                    continue
                try:
                    event = json.loads(payload_str)
                    events.append(event)
                except json.JSONDecodeError:
                    continue
                if event.get("event_type") in ("completed", "failed"):
                    break

    assert len(events) >= 3, f"Expected at least 3 SSE events, got {len(events)}"
    event_types = [e.get("event_type") for e in events]
    assert "agents_resolved" in event_types
    assert "completed" in event_types or "failed" in event_types


# ---------------------------------------------------------------------------
# 7. Mortgage Platform end-to-end (requires --profile uc5)
# ---------------------------------------------------------------------------

async def test_mortgage_platform_trigger_completes(mortgage_platform_client: httpx.AsyncClient):
    """POST /trigger on Mortgage Platform completes the full A2A round-trip."""
    resp = await mortgage_platform_client.post("/trigger", timeout=120.0)
    assert resp.status_code == 200
    result = resp.json()
    assert result["status"] == "completed", f"Expected completed, got: {result}"
    assert result.get("artifact") is not None
    assert result["artifact"]["decision"] in ("approve", "review", "decline")
