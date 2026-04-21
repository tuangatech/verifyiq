# tests/test_phase5_orchestration.py
"""Phase 5 integration tests — orchestration patterns against live Docker stack.

Most critical tests in the project: fan-out concurrency, optional agent handling,
correlation_id continuity, sequential chain to synthesis, use-case routing.

Run:  pytest tests/test_phase5_orchestration.py -v
"""

import asyncio
import json
import time
import uuid

import httpx
import pytest

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
# 1. Full four-agent pipeline (UC-1a)
# ---------------------------------------------------------------------------

async def test_fan_out_all_agents_mortgage_intl(orchestrator_client):
    """UC-1a: mortgage + has_foreign_addr invokes all four agents."""
    body = _verify_request(use_case="mortgage", has_foreign_addr=True)
    task_id, correlation_id, status_data = await _submit_and_wait(orchestrator_client, body)

    assert status_data["status"] == "completed"
    assert status_data["decision"] in VALID_DECISIONS

    tasks = await _get_agent_tasks(orchestrator_client, task_id)
    assert len(tasks) == 4
    by_name = _tasks_by_name(tasks)
    assert set(by_name.keys()) == {"equifax", "employment", "intl", "synthesis"}
    for name, t in by_name.items():
        assert t["status"] == "completed", f"{name} was {t['status']}"
        assert t["correlation_id"] == correlation_id


# ---------------------------------------------------------------------------
# 2. Fan-out concurrency
# ---------------------------------------------------------------------------

async def test_fan_out_concurrency(orchestrator_client):
    """Parallel agents are dispatched concurrently, not sequentially."""
    body = _verify_request(use_case="mortgage", has_foreign_addr=True)
    task_id, _, status_data = await _submit_and_wait(orchestrator_client, body)
    assert status_data["status"] == "completed"

    tasks = await _get_agent_tasks(orchestrator_client, task_id)
    by_name = _tasks_by_name(tasks)

    parallel_names = ["equifax", "employment", "intl"]
    started_times = []
    for name in parallel_names:
        t = by_name[name]
        assert t["started_at"] is not None, f"{name} missing started_at"
        started_times.append(t["started_at"])

    # All three should have been dispatched within 2 seconds of each other
    started_times.sort()
    earliest = started_times[0]
    latest = started_times[-1]
    # ISO timestamps are lexicographically comparable; parse for numeric diff
    from datetime import datetime
    t0 = datetime.fromisoformat(earliest)
    t1 = datetime.fromisoformat(latest)
    diff = (t1 - t0).total_seconds()
    assert diff < 2.0, f"Parallel agents started {diff:.1f}s apart — not concurrent"


# ---------------------------------------------------------------------------
# 3. Optional agent skipped — rental (UC-2)
# ---------------------------------------------------------------------------

async def test_optional_agent_skipped_rental(orchestrator_client):
    """UC-2: rental → intl skipped, equifax+employment+synthesis completed."""
    body = _verify_request(use_case="rental", has_foreign_addr=False)
    task_id, _, status_data = await _submit_and_wait(orchestrator_client, body)

    assert status_data["status"] == "completed"

    tasks = await _get_agent_tasks(orchestrator_client, task_id)
    assert len(tasks) == 4
    by_name = _tasks_by_name(tasks)
    assert by_name["intl"]["status"] == "skipped"
    assert by_name["equifax"]["status"] == "completed"
    assert by_name["employment"]["status"] == "completed"
    assert by_name["synthesis"]["status"] == "completed"


# ---------------------------------------------------------------------------
# 4. Equifax skipped — hire (UC-4)
# ---------------------------------------------------------------------------

async def test_equifax_skipped_hire(orchestrator_client):
    """UC-4: hire + has_foreign_addr → equifax skipped, intl+employment completed."""
    body = _verify_request(use_case="hire", has_foreign_addr=True)
    task_id, _, status_data = await _submit_and_wait(orchestrator_client, body)

    assert status_data["status"] == "completed"

    tasks = await _get_agent_tasks(orchestrator_client, task_id)
    by_name = _tasks_by_name(tasks)
    assert by_name["equifax"]["status"] == "skipped"
    assert by_name["employment"]["status"] == "completed"
    assert by_name["intl"]["status"] == "completed"
    assert by_name["synthesis"]["status"] == "completed"


# ---------------------------------------------------------------------------
# 5. Intl unavailable still completes pipeline
# ---------------------------------------------------------------------------

async def test_intl_unavailable_still_completes(orchestrator_client):
    """Pipeline completes even when intl is dispatched — unavailable or not."""
    body = _verify_request(
        use_case="mortgage", has_foreign_addr=True, subject_name="Domestic Person"
    )
    task_id, _, status_data = await _submit_and_wait(orchestrator_client, body)

    assert status_data["status"] == "completed"
    assert status_data["decision"] in VALID_DECISIONS

    tasks = await _get_agent_tasks(orchestrator_client, task_id)
    by_name = _tasks_by_name(tasks)
    assert by_name["intl"]["status"] == "completed"


# ---------------------------------------------------------------------------
# 6. correlation_id flows through all tasks
# ---------------------------------------------------------------------------

async def test_correlation_id_flows_through_all_tasks(orchestrator_client):
    """Every agent_task row shares the same correlation_id from POST /verify."""
    body = _verify_request(use_case="mortgage", has_foreign_addr=True)
    task_id, correlation_id, status_data = await _submit_and_wait(orchestrator_client, body)
    assert status_data["status"] == "completed"

    tasks = await _get_agent_tasks(orchestrator_client, task_id)
    for t in tasks:
        assert t["correlation_id"] == correlation_id, (
            f"Agent {t['agent_name']} has correlation_id={t['correlation_id']}, "
            f"expected {correlation_id}"
        )
        assert t["correlation_id"] is not None


# ---------------------------------------------------------------------------
# 7. Synthesis receives outcome bundle
# ---------------------------------------------------------------------------

async def test_synthesis_receives_outcome_bundle(orchestrator_client):
    """Synthesis task input contains outcomes list and use_case."""
    body = _verify_request(use_case="mortgage", has_foreign_addr=True)
    task_id, _, status_data = await _submit_and_wait(orchestrator_client, body)
    assert status_data["status"] == "completed"

    tasks = await _get_agent_tasks(orchestrator_client, task_id)
    by_name = _tasks_by_name(tasks)
    synthesis_task = by_name["synthesis"]
    assert synthesis_task["status"] == "completed"

    synthesis_input = json.loads(synthesis_task["input"])
    assert "outcomes" in synthesis_input
    assert isinstance(synthesis_input["outcomes"], list)

    outcome_agents = {o["agent_name"] for o in synthesis_input["outcomes"]}
    assert "equifax" in outcome_agents
    assert "employment" in outcome_agents
    assert "intl" in outcome_agents
    assert "use_case" in synthesis_input


# ---------------------------------------------------------------------------
# 8. Decision stored on request
# ---------------------------------------------------------------------------

async def test_synthesis_decision_stored_on_request(orchestrator_client):
    """Decision from synthesis is written to the verification_request row."""
    body = _verify_request(use_case="mortgage", has_foreign_addr=True)
    task_id, _, status_data = await _submit_and_wait(orchestrator_client, body)

    assert status_data["status"] == "completed"
    assert status_data["decision"] in VALID_DECISIONS
    assert status_data["completed_at"] is not None


# ---------------------------------------------------------------------------
# 9. Auto use case routes correctly (UC-3)
# ---------------------------------------------------------------------------

async def test_auto_use_case_routes_correctly(orchestrator_client):
    """UC-3: auto → equifax+employment+synthesis completed, intl skipped."""
    body = _verify_request(use_case="auto", has_foreign_addr=False)
    task_id, _, status_data = await _submit_and_wait(orchestrator_client, body)

    assert status_data["status"] == "completed"

    tasks = await _get_agent_tasks(orchestrator_client, task_id)
    by_name = _tasks_by_name(tasks)
    assert by_name["equifax"]["status"] == "completed"
    assert by_name["employment"]["status"] == "completed"
    assert by_name["intl"]["status"] == "skipped"
    assert by_name["synthesis"]["status"] == "completed"


# ---------------------------------------------------------------------------
# 10. Multiple requests are independent
# ---------------------------------------------------------------------------

async def test_multiple_requests_independent(orchestrator_client):
    """Two concurrent requests have independent correlation_ids and task sets."""
    body1 = _verify_request(use_case="mortgage", has_foreign_addr=True, subject_name="Subject A")
    body2 = _verify_request(use_case="rental", has_foreign_addr=False, subject_name="Subject B")

    (task_id1, corr1, data1), (task_id2, corr2, data2) = await asyncio.gather(
        _submit_and_wait(orchestrator_client, body1),
        _submit_and_wait(orchestrator_client, body2),
    )

    assert data1["status"] == "completed"
    assert data2["status"] == "completed"
    assert task_id1 != task_id2
    assert corr1 != corr2

    tasks1 = await _get_agent_tasks(orchestrator_client, task_id1)
    tasks2 = await _get_agent_tasks(orchestrator_client, task_id2)

    for t in tasks1:
        assert t["correlation_id"] == corr1
    for t in tasks2:
        assert t["correlation_id"] == corr2


# ---------------------------------------------------------------------------
# 11. Skipped agent task row written
# ---------------------------------------------------------------------------

async def test_skipped_agent_task_row_written(orchestrator_client):
    """Skipped agents get a DB row with status=skipped and attempt=0."""
    body = _verify_request(use_case="rental", has_foreign_addr=False)
    task_id, correlation_id, status_data = await _submit_and_wait(orchestrator_client, body)
    assert status_data["status"] == "completed"

    tasks = await _get_agent_tasks(orchestrator_client, task_id)
    by_name = _tasks_by_name(tasks)
    intl_task = by_name["intl"]

    assert intl_task["status"] == "skipped"
    assert intl_task["correlation_id"] == correlation_id
    assert intl_task["attempt"] == 0


# ---------------------------------------------------------------------------
# 12. Full pipeline end-to-end risk assessment
# ---------------------------------------------------------------------------

async def test_full_pipeline_with_weak_profile(orchestrator_client):
    """End-to-end flow completes with decision and risk_score present."""
    body = _verify_request(
        use_case="mortgage", has_foreign_addr=True, subject_name="Risky McRiskface"
    )
    task_id, _, status_data = await _submit_and_wait(orchestrator_client, body)

    assert status_data["status"] == "completed"
    assert status_data["decision"] in VALID_DECISIONS

    tasks = await _get_agent_tasks(orchestrator_client, task_id)
    by_name = _tasks_by_name(tasks)
    synthesis_artifact = json.loads(by_name["synthesis"]["artifact"])
    assert "risk_score" in synthesis_artifact
