# tests/test_phase3_a2a.py
"""
Phase 3 integration tests -- A2A task protocol round-trips, stub artifacts.

Run against the live Docker Compose stack:
    pytest tests/test_phase3_a2a.py -v

All 10 tests must pass before Phase 4.
"""

import asyncio
import uuid

import pytest
import httpx

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _task(skill: str, subject_id: str = "test-S001", extra_input: dict | None = None) -> dict:
    """Build an A2ATask-shaped dict for testing."""
    return {
        "task_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "skill": skill,
        "input": {
            "subject_id": subject_id,
            "subject_name": "Test Subject",
            "use_case": "mortgage",
            "has_foreign_addr": True,
            **(extra_input or {}),
        },
        "timeout_ms": 30000,
        "attempt": 1,
    }


# ---------------------------------------------------------------------------
# 1-4: Each data agent returns completed with correct fields
# ---------------------------------------------------------------------------

async def test_equifax_task_send_returns_completed(equifax_client: httpx.AsyncClient):
    """POST /tasks/send to Equifax returns completed with artifact."""
    task = _task("credit_score")
    resp = await equifax_client.post("/tasks/send", json=task)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "completed"
    assert data["task_id"] == task["task_id"]
    assert data["correlation_id"] == task["correlation_id"]
    assert data["artifact"] is not None
    assert data["started_at"] is not None
    assert data["ended_at"] is not None


async def test_employment_task_send_returns_completed(employment_client: httpx.AsyncClient):
    """POST /tasks/send to Employment returns completed with artifact."""
    task = _task("employment_status")
    resp = await employment_client.post("/tasks/send", json=task)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "completed"
    assert data["task_id"] == task["task_id"]
    assert data["correlation_id"] == task["correlation_id"]
    assert data["artifact"] is not None
    assert data["started_at"] is not None
    assert data["ended_at"] is not None


async def test_intl_task_send_returns_completed(intl_client: httpx.AsyncClient):
    """POST /tasks/send to International returns completed with artifact."""
    task = _task("international_credit_score")
    resp = await intl_client.post("/tasks/send", json=task)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "completed"
    assert data["task_id"] == task["task_id"]
    assert data["correlation_id"] == task["correlation_id"]
    assert data["artifact"] is not None
    assert data["started_at"] is not None
    assert data["ended_at"] is not None


async def test_synthesis_task_send_returns_completed(synthesis_client: httpx.AsyncClient):
    """POST /tasks/send to Synthesis returns completed with valid decision."""
    task = _task("risk_synthesis", extra_input={"outcomes": []})
    resp = await synthesis_client.post("/tasks/send", json=task)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "completed"
    assert data["task_id"] == task["task_id"]
    assert data["correlation_id"] == task["correlation_id"]
    assert data["artifact"] is not None
    assert data["artifact"]["decision"] in ["approve", "review", "decline"]


# ---------------------------------------------------------------------------
# 5: correlation_id echo
# ---------------------------------------------------------------------------

async def test_correlation_id_echoed_back(equifax_client: httpx.AsyncClient):
    """correlation_id sent to agent is echoed back unchanged."""
    task = _task("credit_score")
    task["correlation_id"] = "corr-phase3-echo-test"
    resp = await equifax_client.post("/tasks/send", json=task)
    assert resp.status_code == 200, resp.text
    assert resp.json()["correlation_id"] == "corr-phase3-echo-test"


# ---------------------------------------------------------------------------
# 6-7: GET task retrieval and 404
# ---------------------------------------------------------------------------

async def test_get_task_after_completion(equifax_client: httpx.AsyncClient):
    """GET /tasks/{task_id} retrieves a previously completed task."""
    task = _task("credit_score")
    post_resp = await equifax_client.post("/tasks/send", json=task)
    assert post_resp.status_code == 200
    task_id = post_resp.json()["task_id"]

    get_resp = await equifax_client.get(f"/tasks/{task_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["status"] == "completed"
    assert data["ended_at"] is not None


async def test_get_unknown_task_returns_404(equifax_client: httpx.AsyncClient):
    """GET /tasks/{unknown_id} returns 404."""
    resp = await equifax_client.get("/tasks/no-such-task-abc123")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 8-10: Orchestrator /verify end-to-end
# ---------------------------------------------------------------------------

async def test_verify_returns_task_id(orchestrator_client: httpx.AsyncClient):
    """POST /verify returns task_id, correlation_id, and stream_url."""
    body = {
        "subject_name": "Jane Doe",
        "subject_id": "S001",
        "use_case": "mortgage",
        "has_foreign_addr": True,
        "consent": True,
    }
    resp = await orchestrator_client.post("/verify", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["task_id"]
    assert data["correlation_id"]
    assert "stream_url" in data


async def test_verify_task_completes(orchestrator_client: httpx.AsyncClient):
    """POST /verify followed by polling reaches status completed."""
    body = {
        "subject_name": "Jane Doe",
        "subject_id": "S002",
        "use_case": "mortgage",
        "has_foreign_addr": True,
        "consent": True,
    }
    resp = await orchestrator_client.post("/verify", json=body)
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    for _ in range(20):
        await asyncio.sleep(0.5)
        poll = await orchestrator_client.get(f"/verify/{task_id}")
        assert poll.status_code == 200
        status = poll.json()["status"]
        if status in ("completed", "failed"):
            break

    assert status == "completed", f"Expected completed, got {status}"


async def test_agent_task_written_to_db(orchestrator_client: httpx.AsyncClient):
    """POST /verify produces an agent_tasks row with correct correlation_id."""
    body = {
        "subject_name": "Jane Doe",
        "subject_id": "S003",
        "use_case": "mortgage",
        "has_foreign_addr": True,
        "consent": True,
    }
    resp = await orchestrator_client.post("/verify", json=body)
    assert resp.status_code == 200
    data = resp.json()
    task_id = data["task_id"]
    correlation_id = data["correlation_id"]

    for _ in range(20):
        await asyncio.sleep(0.5)
        poll = await orchestrator_client.get(f"/verify/{task_id}")
        if poll.json()["status"] in ("completed", "failed"):
            break

    assert poll.json()["status"] == "completed"

    tasks_resp = await orchestrator_client.get(f"/verify/{task_id}/tasks")
    assert tasks_resp.status_code == 200
    tasks = tasks_resp.json()
    assert len(tasks) == 1, f"Expected 1 agent task, got {len(tasks)}: {tasks}"
    assert tasks[0]["agent_name"] == "equifax"
    assert tasks[0]["status"] == "completed"
    assert tasks[0]["correlation_id"] == correlation_id
