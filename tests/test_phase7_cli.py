# tests/test_phase7_cli.py
"""
Phase 7 integration tests — CLI endpoints + end-to-end use case verification.

Run: pytest tests/test_phase7_cli.py -v

All 10 tests must pass before proceeding.
"""

import asyncio
import json
import time
import uuid

import pytest
import httpx

from conftest import AUTH_HEADERS

pytestmark = pytest.mark.asyncio

VALID_DECISIONS = {"approve", "review", "decline"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_request(
    use_case: str = "mortgage",
    has_foreign_addr: bool = True,
    subject_name: str = "Test Subject",
) -> dict:
    """Build a VerificationRequest body."""
    return {
        "subject_name": subject_name,
        "subject_id": f"S-{uuid.uuid4().hex[:6]}",
        "use_case": use_case,
        "has_foreign_addr": has_foreign_addr,
        "consent": True,
    }


async def _submit_and_wait(
    client: httpx.AsyncClient, body: dict, timeout: int = 60
) -> tuple[str, str, dict]:
    """Submit a verify request and poll until terminal status or timeout."""
    resp = await client.post("/verify", json=body, headers=AUTH_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    task_id = data["task_id"]
    correlation_id = data["correlation_id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        status_resp = await client.get(f"/verify/{task_id}")
        status_data = status_resp.json()
        if status_data["status"] in ("completed", "failed"):
            return task_id, correlation_id, status_data
        await asyncio.sleep(0.5)

    raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")


# ---------------------------------------------------------------------------
# Endpoint Tests (4 tests)
# ---------------------------------------------------------------------------

# Test 1: History returns recent requests
async def test_history_returns_recent_requests(orchestrator_client: httpx.AsyncClient):
    """Submit two requests with different use cases, verify history returns both."""
    body1 = _verify_request(use_case="mortgage", has_foreign_addr=True, subject_name="History A")
    body2 = _verify_request(use_case="rental", has_foreign_addr=False, subject_name="History B")

    await asyncio.gather(
        _submit_and_wait(orchestrator_client, body1),
        _submit_and_wait(orchestrator_client, body2),
    )

    resp = await orchestrator_client.get("/verify/history", params={"limit": 10})
    assert resp.status_code == 200
    items = resp.json()

    assert isinstance(items, list)
    assert len(items) >= 2

    required_keys = {"task_id", "subject_name", "use_case", "status", "created_at"}
    for item in items:
        assert required_keys.issubset(item.keys()), f"Missing keys in {item.keys()}"

    # Most recent first — created_at should be descending
    timestamps = [item["created_at"] for item in items]
    assert timestamps == sorted(timestamps, reverse=True), "History not ordered by created_at DESC"

    subject_names = {item["subject_name"] for item in items}
    assert "History A" in subject_names
    assert "History B" in subject_names


# Test 2: History respects limit
async def test_history_respects_limit(orchestrator_client: httpx.AsyncClient):
    """Submit 3 requests, request limit=2, get exactly 2."""
    bodies = [
        _verify_request(use_case="mortgage", has_foreign_addr=False, subject_name=f"Limit-{i}")
        for i in range(3)
    ]
    await asyncio.gather(*[_submit_and_wait(orchestrator_client, b) for b in bodies])

    resp = await orchestrator_client.get("/verify/history", params={"limit": 2})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2


# Test 3: Full returns complete dump
async def test_full_returns_complete_dump(orchestrator_client: httpx.AsyncClient):
    """Full dump contains request, agent_tasks, sse_events with parsed artifacts."""
    body = _verify_request(use_case="mortgage", has_foreign_addr=True, subject_name="Full Dump")
    task_id, correlation_id, status_data = await _submit_and_wait(orchestrator_client, body)
    assert status_data["status"] == "completed"

    resp = await orchestrator_client.get(f"/verify/{task_id}/full")
    assert resp.status_code == 200
    data = resp.json()

    assert "request" in data
    assert "agent_tasks" in data
    assert "sse_events" in data

    req = data["request"]
    assert req["task_id"] == task_id
    assert req["correlation_id"] == correlation_id
    assert req["status"] == "completed"
    assert req["decision"] in VALID_DECISIONS

    agent_tasks = data["agent_tasks"]
    assert len(agent_tasks) == 4
    agent_names = {t["agent_name"] for t in agent_tasks}
    assert agent_names == {"equifax", "employment", "intl", "synthesis"}

    for task in agent_tasks:
        assert "agent_name" in task
        assert "status" in task
        assert "artifact" in task or "error" in task

    # Artifacts on completed tasks must be parsed dicts, not JSON strings
    for task in agent_tasks:
        if task["status"] == "completed" and task["artifact"] is not None:
            assert isinstance(task["artifact"], dict), (
                f"{task['agent_name']} artifact is {type(task['artifact'])}, expected dict"
            )

    sse_events = data["sse_events"]
    assert len(sse_events) >= 4


# Test 4: Full returns 404 for unknown task
async def test_full_returns_404_for_unknown_task(orchestrator_client: httpx.AsyncClient):
    """GET /verify/nonexistent-id/full returns 404."""
    resp = await orchestrator_client.get("/verify/nonexistent-id/full")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# End-to-End Use Case Tests (5 tests)
# ---------------------------------------------------------------------------

async def _get_full_dump(client: httpx.AsyncClient, task_id: str) -> dict:
    """Fetch the full artifact dump for a completed task."""
    resp = await client.get(f"/verify/{task_id}/full")
    assert resp.status_code == 200
    return resp.json()


def _tasks_by_name(agent_tasks: list[dict]) -> dict[str, dict]:
    """Index agent tasks by agent_name."""
    return {t["agent_name"]: t for t in agent_tasks}


# Test 5: UC-1a — Full four-agent pipeline
async def test_uc1a_mortgage_intl_all_agents(orchestrator_client: httpx.AsyncClient):
    """UC-1a: mortgage + has_foreign_addr → all four agents completed."""
    body = _verify_request(use_case="mortgage", has_foreign_addr=True, subject_name="Nguyen Minh Tuan")
    task_id, _, status_data = await _submit_and_wait(orchestrator_client, body)

    assert status_data["status"] == "completed"
    assert status_data["decision"] in VALID_DECISIONS

    data = await _get_full_dump(orchestrator_client, task_id)
    by_name = _tasks_by_name(data["agent_tasks"])

    assert len(by_name) == 4
    assert set(by_name.keys()) == {"equifax", "employment", "intl", "synthesis"}
    for name, task in by_name.items():
        assert task["status"] == "completed", f"{name} was {task['status']}"

    # Intl artifact has data_availability field
    intl_artifact = by_name["intl"]["artifact"]
    assert isinstance(intl_artifact, dict)
    assert "data_availability" in intl_artifact
    assert intl_artifact["data_availability"] in ("full", "partial", "unavailable")

    # Synthesis artifact has required fields
    synth_artifact = by_name["synthesis"]["artifact"]
    assert isinstance(synth_artifact, dict)
    for key in ("decision", "confidence", "risk_score", "decision_factors", "reasoning_summary"):
        assert key in synth_artifact, f"Missing '{key}' in synthesis artifact"


# Test 6: UC-1b — Intl dispatched, pipeline completes regardless
async def test_uc1b_mortgage_domestic_intl_unavailable(orchestrator_client: httpx.AsyncClient):
    """UC-1b: mortgage + has_foreign_addr + domestic name → intl dispatched, pipeline completes."""
    body = _verify_request(
        use_case="mortgage", has_foreign_addr=True, subject_name="Sarah Johnson"
    )
    task_id, correlation_id, status_data = await _submit_and_wait(orchestrator_client, body)

    assert status_data["status"] == "completed"
    assert status_data["decision"] in VALID_DECISIONS

    data = await _get_full_dump(orchestrator_client, task_id)
    by_name = _tasks_by_name(data["agent_tasks"])

    assert len(by_name) == 4
    # Intl was dispatched (not skipped)
    assert by_name["intl"]["status"] == "completed"
    # All tasks share the same correlation_id
    for task in data["agent_tasks"]:
        assert task["correlation_id"] == correlation_id


# Test 7: UC-2 — Intl skipped entirely
async def test_uc2_rental_intl_skipped(orchestrator_client: httpx.AsyncClient):
    """UC-2: rental + no foreign addr → intl skipped, others complete."""
    body = _verify_request(use_case="rental", has_foreign_addr=False, subject_name="Marcus Williams")
    task_id, _, status_data = await _submit_and_wait(orchestrator_client, body)

    assert status_data["status"] == "completed"

    data = await _get_full_dump(orchestrator_client, task_id)
    by_name = _tasks_by_name(data["agent_tasks"])

    assert by_name["equifax"]["status"] == "completed"
    assert by_name["employment"]["status"] == "completed"
    assert by_name["intl"]["status"] == "skipped"
    assert by_name["synthesis"]["status"] == "completed"

    # SSE events should contain agent_skipped for intl
    sse_events = data["sse_events"]
    event_payloads = [
        e["payload"] if isinstance(e["payload"], dict) else json.loads(e["payload"])
        for e in sse_events
    ]
    skipped_events = [
        p for p in event_payloads
        if p.get("event_type") == "agent_skipped" and p.get("agent") == "intl"
    ]
    assert len(skipped_events) > 0, "No agent_skipped SSE event for intl"

    # No agent_started for intl
    started_events = [
        p for p in event_payloads
        if p.get("event_type") == "agent_started" and p.get("agent") == "intl"
    ]
    assert len(started_events) == 0, "Unexpected agent_started for intl"


# Test 8: UC-3 — Auto loan completes
async def test_uc3_auto_completes(orchestrator_client: httpx.AsyncClient):
    """UC-3: auto + no foreign addr → equifax+employment+synthesis completed, intl skipped."""
    body = _verify_request(use_case="auto", has_foreign_addr=False, subject_name="Jennifer Chen")
    task_id, _, status_data = await _submit_and_wait(orchestrator_client, body)

    assert status_data["status"] == "completed"

    data = await _get_full_dump(orchestrator_client, task_id)
    by_name = _tasks_by_name(data["agent_tasks"])

    assert by_name["equifax"]["status"] == "completed"
    assert by_name["employment"]["status"] == "completed"
    assert by_name["intl"]["status"] == "skipped"
    assert by_name["synthesis"]["status"] == "completed"

    synth_artifact = by_name["synthesis"]["artifact"]
    assert isinstance(synth_artifact, dict)
    assert "decision" in synth_artifact
    assert "reasoning_summary" in synth_artifact


# Test 9: UC-4 — Equifax skipped, intl required
async def test_uc4_hire_equifax_skipped(orchestrator_client: httpx.AsyncClient):
    """UC-4: hire + has_foreign_addr → equifax skipped, intl+employment+synthesis completed."""
    body = _verify_request(use_case="hire", has_foreign_addr=True, subject_name="Raj Patel")
    task_id, _, status_data = await _submit_and_wait(orchestrator_client, body)

    assert status_data["status"] == "completed"

    data = await _get_full_dump(orchestrator_client, task_id)
    by_name = _tasks_by_name(data["agent_tasks"])

    assert by_name["equifax"]["status"] == "skipped"
    assert by_name["employment"]["status"] == "completed"
    assert by_name["intl"]["status"] == "completed"
    assert by_name["synthesis"]["status"] == "completed"

    # No agent_started for equifax in SSE events
    sse_events = data["sse_events"]
    event_payloads = [
        e["payload"] if isinstance(e["payload"], dict) else json.loads(e["payload"])
        for e in sse_events
    ]
    started_events = [
        p for p in event_payloads
        if p.get("event_type") == "agent_started" and p.get("agent") == "equifax"
    ]
    assert len(started_events) == 0, "Unexpected agent_started for equifax"


# ---------------------------------------------------------------------------
# Correlation ID Consistency Test (1 test)
# ---------------------------------------------------------------------------

# Test 10: Correlation ID consistent across full dump
async def test_correlation_id_consistent_in_full_dump(orchestrator_client: httpx.AsyncClient):
    """Every correlation_id in the full dump matches the request's correlation_id."""
    body = _verify_request(use_case="mortgage", has_foreign_addr=True, subject_name="Corr Check")
    task_id, correlation_id, status_data = await _submit_and_wait(orchestrator_client, body)
    assert status_data["status"] == "completed"

    data = await _get_full_dump(orchestrator_client, task_id)

    # Request correlation_id
    assert data["request"]["correlation_id"] == correlation_id

    # All agent tasks share the same correlation_id
    for task in data["agent_tasks"]:
        assert task["correlation_id"] == correlation_id, (
            f"Agent {task['agent_name']} has correlation_id={task['correlation_id']}, "
            f"expected {correlation_id}"
        )
        assert task["correlation_id"] is not None

    # All SSE events share the same correlation_id
    for event in data["sse_events"]:
        assert event["correlation_id"] == correlation_id, (
            f"SSE event has correlation_id={event['correlation_id']}, "
            f"expected {correlation_id}"
        )
        assert event["correlation_id"] is not None
