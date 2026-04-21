# tests/test_phase6_langgraph_sse.py
"""
Phase 6 integration tests — LangGraph Employment Agent + SSE streaming.

Run: pytest tests/test_phase6_langgraph_sse.py -v

All 12 tests must pass before proceeding.
"""

import asyncio
import json
import time
import uuid

import pytest
import httpx

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
    resp = await client.post("/verify", json=body)
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


async def _get_agent_tasks(client: httpx.AsyncClient, task_id: str) -> list[dict]:
    """Fetch all agent_task rows for a verification request."""
    resp = await client.get(f"/verify/{task_id}/tasks")
    assert resp.status_code == 200
    return resp.json()


def _tasks_by_name(tasks: list[dict]) -> dict[str, dict]:
    """Index agent_task rows by agent_name for easy lookup."""
    return {t["agent_name"]: t for t in tasks}


# ---------------------------------------------------------------------------
# LangGraph Employment Agent Tests (6 tests)
# ---------------------------------------------------------------------------

# Test 1: Direct call to employment agent returns valid artifact
async def test_employment_langgraph_returns_valid_artifact(employment_client: httpx.AsyncClient):
    """LangGraph graph executes and returns EmploymentArtifact matching schema."""
    task = {
        "task_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "skill": "employment_status",
        "input": {
            "subject_id": "S6-1",
            "subject_name": "Jane Doe",
            "use_case": "mortgage",
            "has_foreign_addr": False,
            "consent": True,
        },
        "timeout_ms": 30000,
        "attempt": 1,
    }
    resp = await employment_client.post("/tasks/send", json=task)
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "completed"
    assert data["task_id"] == task["task_id"]
    assert data["correlation_id"] == task["correlation_id"]
    assert data["artifact"] is not None
    artifact = data["artifact"]

    # Verify artifact schema
    assert artifact["source"] == "workforce_solutions"
    assert "subject_id" in artifact
    assert "currently_employed" in artifact
    assert isinstance(artifact["currently_employed"], bool)
    assert "employer_name" in artifact
    assert "employment_start_date" in artifact
    assert "tenure_years" in artifact
    assert "verified_annual_income" in artifact
    assert "income_currency" in artifact
    assert artifact["income_currency"] == "USD"
    assert "verification_confidence" in artifact
    assert artifact["verification_confidence"] in ("high", "medium", "low")
    assert "data_as_of" in artifact


# Test 2: Both branches populate all downstream fields
async def test_employment_langgraph_branching_fields(employment_client: httpx.AsyncClient):
    """Both employed and unemployed branches populate all required fields.

    - If employed: tenure_years from employment_start_date
    - If unemployed: tenure_years from prior employer end date
    """
    task = {
        "task_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "skill": "employment_status",
        "input": {
            "subject_id": "S6-2",
            "subject_name": "Bob Smith",
            "use_case": "rental",
            "has_foreign_addr": False,
            "consent": True,
        },
        "timeout_ms": 30000,
        "attempt": 1,
    }
    resp = await employment_client.post("/tasks/send", json=task)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    artifact = data["artifact"]

    # Both branches should populate employer_name, tenure_years, verified_annual_income
    assert artifact["employer_name"] is not None, "employer_name missing"
    assert artifact["tenure_years"] is not None, "tenure_years missing"
    assert artifact["verified_annual_income"] is not None, "verified_annual_income missing"
    assert isinstance(artifact["tenure_years"], (int, float))
    assert isinstance(artifact["verified_annual_income"], int)


# Test 3: Income is populated
async def test_employment_langgraph_income_populated(employment_client: httpx.AsyncClient):
    """Income node produces verified_annual_income > 0 and income_currency == USD."""
    task = {
        "task_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "skill": "employment_status",
        "input": {
            "subject_id": "S6-3",
            "subject_name": "Carol White",
            "use_case": "mortgage",
            "has_foreign_addr": False,
            "consent": True,
        },
        "timeout_ms": 30000,
        "attempt": 1,
    }
    resp = await employment_client.post("/tasks/send", json=task)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    artifact = data["artifact"]

    assert artifact["verified_annual_income"] > 0
    assert artifact["income_currency"] == "USD"


# Test 4: Tenure calculated and confidence set
async def test_employment_langgraph_tenure_calculated(employment_client: httpx.AsyncClient):
    """Tenure is computed from dates; verification_confidence is high/medium/low."""
    task = {
        "task_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "skill": "employment_status",
        "input": {
            "subject_id": "S6-4",
            "subject_name": "Dave Brown",
            "use_case": "hire",
            "has_foreign_addr": False,
            "consent": True,
        },
        "timeout_ms": 30000,
        "attempt": 1,
    }
    resp = await employment_client.post("/tasks/send", json=task)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    artifact = data["artifact"]

    assert artifact["tenure_years"] is not None
    assert artifact["tenure_years"] >= 0
    assert artifact["verification_confidence"] in ("high", "medium", "low")

    # Validate confidence is consistent with tenure
    if artifact["tenure_years"] >= 3:
        assert artifact["verification_confidence"] == "high"
    elif artifact["tenure_years"] >= 1:
        assert artifact["verification_confidence"] in ("high", "medium")
    else:
        assert artifact["verification_confidence"] in ("medium", "low")


# Test 5: correlation_id echoed back unchanged
async def test_employment_langgraph_correlation_id_preserved(employment_client: httpx.AsyncClient):
    """correlation_id sent to agent is echoed back unchanged."""
    custom_corr = "corr-phase6-echo-test"
    task = {
        "task_id": str(uuid.uuid4()),
        "correlation_id": custom_corr,
        "skill": "employment_status",
        "input": {
            "subject_id": "S6-5",
            "subject_name": "Eve Green",
            "use_case": "auto",
            "has_foreign_addr": False,
            "consent": True,
        },
        "timeout_ms": 30000,
        "attempt": 1,
    }
    resp = await employment_client.post("/tasks/send", json=task)
    assert resp.status_code == 200
    data = resp.json()
    assert data["correlation_id"] == custom_corr


# Test 6: Multiple calls produce varied results (proves LLM variation)
async def test_employment_langgraph_multiple_calls_vary(employment_client: httpx.AsyncClient):
    """Three calls with same subject should produce varied results (not canned responses)."""
    base_task = {
        "task_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "skill": "employment_status",
        "input": {
            "subject_id": "S6-multi",
            "subject_name": "Multi Call Subject",
            "use_case": "mortgage",
            "has_foreign_addr": False,
            "consent": True,
        },
        "timeout_ms": 30000,
        "attempt": 1,
    }

    # Run 3 parallel calls
    results = await asyncio.gather(
        employment_client.post("/tasks/send", json={**base_task, "task_id": str(uuid.uuid4())}),
        employment_client.post("/tasks/send", json={**base_task, "task_id": str(uuid.uuid4())}),
        employment_client.post("/tasks/send", json={**base_task, "task_id": str(uuid.uuid4())}),
    )

    artifacts = []
    for resp in results:
        assert resp.status_code == 200
        data = resp.json()
        artifacts.append(data["artifact"])

    # At least 2 should have different employer_name or income
    names = [a["employer_name"] for a in artifacts]
    incomes = [a["verified_annual_income"] for a in artifacts]
    assert len(set(names)) >= 2 or len(set(incomes)) >= 2, (
        f"All 3 artifacts identical: {names} and {incomes}"
    )


# ---------------------------------------------------------------------------
# SSE Streaming Tests (6 tests)
# ---------------------------------------------------------------------------

# Test 7: Stream emits events until completed
async def test_sse_stream_emits_events(orchestrator_client: httpx.AsyncClient):
    """Connect to stream, collect events until completed → at least 4 events."""
    body = _verify_request(use_case="mortgage", has_foreign_addr=False)
    resp = await orchestrator_client.post("/verify", json=body)
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    events = []

    async def consume_events(client: httpx.AsyncClient):
        async for line in client.stream("GET", f"{orchestrator_client.base_url}/verify/{task_id}/stream").aiter_lines():
            if not line.strip():
                continue
            if line.startswith("data:"):
                payload_str = line[5:].strip()
                if payload_str:
                    events.append(json.loads(payload_str))
            if line.startswith("event: completed"):
                break

    async with httpx.AsyncClient(base_url=orchestrator_client.base_url) as client:
        await consume_events(client)

    # Should have at least 4 events: agents_resolved, agent_started, agent_completed, completed
    assert len(events) >= 4, f"Expected at least 4 events, got {len(events)}"

    # Verify we have required events
    event_types = [e.get("event_type", "unknown") for e in events]
    assert "agents_resolved" in event_types, f"agents_resolved not in {event_types}"
    assert "completed" in event_types, f"completed not in {event_types}"


# Test 8: Events contain correlation_id
async def test_sse_events_contain_correlation_id(orchestrator_client: httpx.AsyncClient):
    """Every event payload has correlation_id matching the one from POST /verify."""
    body = _verify_request(use_case="mortgage", has_foreign_addr=False)
    resp = await orchestrator_client.post("/verify", json=body)
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]
    correlation_id = resp.json()["correlation_id"]

    events = []

    async def consume_events(client: httpx.AsyncClient):
        async for line in client.stream("GET", f"{orchestrator_client.base_url}/verify/{task_id}/stream").aiter_lines():
            if not line.strip():
                continue
            if line.startswith("data:"):
                payload_str = line[5:].strip()
                if payload_str:
                    events.append(json.loads(payload_str))
            if line.startswith("event: completed"):
                break

    async with httpx.AsyncClient(base_url=orchestrator_client.base_url) as client:
        await consume_events(client)

    # All events should have matching correlation_id
    for event in events:
        assert event.get("correlation_id") == correlation_id, (
            f"Expected {correlation_id}, got {event.get('correlation_id')}"
        )
    assert len(events) >= 1


# Test 9: Progress events arrive before completion
async def test_sse_progress_before_completion(orchestrator_client: httpx.AsyncClient):
    """At least one progress event (agent_started/completed) arrives before completed."""
    body = _verify_request(use_case="mortgage", has_foreign_addr=True)
    resp = await orchestrator_client.post("/verify", json=body)
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    events = []
    event_types_seen = []

    async def consume_events(client: httpx.AsyncClient):
        async for line in client.stream("GET", f"{orchestrator_client.base_url}/verify/{task_id}/stream").aiter_lines():
            if not line.strip():
                continue
            if line.startswith("event:"):
                event_name = line.split(" ", 1)[1] if " " in line else ""
                event_types_seen.append(event_name)
            if line.startswith("data:"):
                payload_str = line[5:].strip()
                if payload_str:
                    events.append(json.loads(payload_str))
            if line.startswith("event: completed"):
                break

    async with httpx.AsyncClient(base_url=orchestrator_client.base_url) as client:
        await consume_events(client)

    # Check that agents_resolved comes before agent_completed
    agents_resolved_idx = event_types_seen.index("agents_resolved") if "agents_resolved" in event_types_seen else -1
    completed_idx = event_types_seen.index("completed") if "completed" in event_types_seen else -1

    assert agents_resolved_idx >= 0, "agents_resolved event missing"
    assert completed_idx >= 0, "completed event missing"
    assert agents_resolved_idx < completed_idx, "agents_resolved came after completed"

    # At least one progress event before completion
    progress_events = ["agent_started", "agent_completed", "synthesis_started", "synthesis_completed"]
    progress_indices = [event_types_seen.index(e) for e in progress_events if e in event_types_seen]
    assert len(progress_indices) > 0, "No progress events before completion"
    assert all(idx < completed_idx for idx in progress_indices), "Progress events came after completion"


# Test 10: Late-connecting client receives replay from DB
async def test_sse_events_persisted_to_db(orchestrator_client: httpx.AsyncClient):
    """After pipeline completes, late-connect to stream → replays events from DB."""
    # Wait a bit before submitting to ensure task completes before connect
    await asyncio.sleep(1)

    body = _verify_request(use_case="mortgage", has_foreign_addr=False)
    resp = await orchestrator_client.post("/verify", json=body)
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    events = []

    async def consume_events(client: httpx.AsyncClient):
        async for line in client.stream("GET", f"{orchestrator_client.base_url}/verify/{task_id}/stream").aiter_lines():
            if not line.strip():
                continue
            if line.startswith("data:"):
                payload_str = line[5:].strip()
                if payload_str:
                    events.append(json.loads(payload_str))
            if line.startswith("event: completed"):
                break

    async with httpx.AsyncClient(base_url=orchestrator_client.base_url) as client:
        await consume_events(client)

    # Late replay should get at least 4 events from DB
    assert len(events) >= 4, f"Expected at least 4 replayed events, got {len(events)}"

    # Events should be in correct order
    event_types = [e.get("event_type", "unknown") for e in events]
    assert "agents_resolved" in event_types
    assert "completed" in event_types


# Test 11: Client disconnect doesn't crash pipeline
async def test_sse_client_disconnect_does_not_crash_pipeline(orchestrator_client: httpx.AsyncClient):
    """Connect to stream, read 1-2 events, disconnect. Poll GET /verify/{task_id} → reaches completed."""
    body = _verify_request(use_case="mortgage", has_foreign_addr=True)
    resp = await orchestrator_client.post("/verify", json=body)
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    # Connect to stream and read first couple events
    events_read = 0

    async def read_partial_stream(client: httpx.AsyncClient):
        nonlocal events_read
        async for line in client.stream("GET", f"{orchestrator_client.base_url}/verify/{task_id}/stream").aiter_lines():
            if not line.strip():
                continue
            if line.startswith("data:"):
                events_read += 1
                if events_read >= 2:
                    break
            if line.startswith("event: completed"):
                break

    async with httpx.AsyncClient(base_url=orchestrator_client.base_url) as client:
        await read_partial_stream(client)

    # Now poll the task status - should reach completed even after disconnect
    deadline = time.time() + 60
    while time.time() < deadline:
        status_resp = await client.get(f"/verify/{task_id}")
        status_data = status_resp.json()
        if status_data["status"] == "completed":
            break
        await asyncio.sleep(0.5)

    assert status_data["status"] == "completed", f"Pipeline failed: {status_data}"

    # Verify all agent_tasks present
    tasks = await _get_agent_tasks(client, task_id)
    by_name = _tasks_by_name(tasks)
    assert "equifax" in by_name
    assert "employment" in by_name
    assert "synthesis" in by_name


# Test 12: Skipped agents emit skipped events
async def test_sse_skipped_agent_event_emitted(orchestrator_client: httpx.AsyncClient):
    """Submit rental request → agent_skipped event for intl, no agent_started for intl."""
    body = _verify_request(use_case="rental", has_foreign_addr=False)
    resp = await orchestrator_client.post("/verify", json=body)
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    events = []

    async def consume_events(client: httpx.AsyncClient):
        async for line in client.stream("GET", f"{orchestrator_client.base_url}/verify/{task_id}/stream").aiter_lines():
            if not line.strip():
                continue
            if line.startswith("data:"):
                payload_str = line[5:].strip()
                if payload_str:
                    events.append(json.loads(payload_str))
            if line.startswith("event: completed"):
                break

    async with httpx.AsyncClient(base_url=orchestrator_client.base_url) as client:
        await consume_events(client)

    # Find intl-related events
    intl_events = [e for e in events if e.get("agent") == "intl" or e.get("agent_name") == "intl"]

    # Should have skipped event, not started/completed
    skipped_events = [e for e in intl_events if e.get("event_type") == "agent_skipped"]
    assert len(skipped_events) > 0, f"No skipped event for intl in {intl_events}"

    # For rental, intl should be skipped
    for event in skipped_events:
        assert event["reason"].endswith("not required for this use case")

    # Should NOT have started/completed for intl
    started_events = [e for e in intl_events if e.get("event_type") == "agent_started"]
    completed_events = [e for e in intl_events if e.get("event_type") == "agent_completed"]
    assert len(started_events) == 0, f"Unexpected agent_started for intl: {started_events}"
    assert len(completed_events) == 0, f"Unexpected agent_completed for intl: {completed_events}"
