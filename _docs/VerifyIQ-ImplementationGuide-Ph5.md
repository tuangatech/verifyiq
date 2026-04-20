# VerifyIQ — Implementation Guide
## Phase 5 — Orchestration Patterns
*Companion to Project Specification v1.7*

---

## How to Use This Guide

This guide describes **what to build and how to verify it** — not the full code. Each step tells you what a file should do, what shape it should take, and what behaviour to expect. Use this as the prompt context for Claude Code to generate the actual file contents.

**Conventions:**

- `$` lines are terminal commands you run directly
- **File description blocks** tell Claude Code what to generate — they describe purpose, structure, key logic, and expected behaviour without containing complete source
- ✅ Checklist items at the end of the phase must all pass before proceeding

---

## Phase 5 — Orchestration Patterns

**Goal:** Full orchestration flow working end-to-end — parallel fan-out, timeout enforcement, one-retry-on-5xx, optional agent skip/unavailable logic, sequential chain to Risk Synthesis with full `AgentOutcome` bundle. All `agent_tasks` rows written with correct `correlation_id`, `attempt`, `started_at`, `ended_at`. Most critical phase in the project.

**Estimated time:** 3–4 hours

**Prerequisite:** Phase 4 complete — all four agents produce LLM-generated artifacts. `pytest tests/ -v` passes all Phase 3 and Phase 4 tests.

---

### Step 5.1 — Add Retry Logic to `TaskDispatcher`

**File:** `agents/orchestrator/dispatcher.py` — update existing.

**What changes:** Add one-retry-on-5xx/connection-error with a 1-second delay between attempts. The `attempt` field on the task increments from 1 to 2 on retry.

**Updated `dispatch` signature:**
```
async dispatch(self, agent_url: str, task: A2ATask, agent_name: str) -> AgentOutcome
```

**Logic:**

```
1. First attempt: POST agent_url/tasks/send with task body (attempt=1)
   - On 2xx → parse result, return AgentOutcome(status=result.status, ...)
   - On TimeoutException → return AgentOutcome(status="timed_out",
       error=AgentError(code="TIMEOUT", retryable=True))
       No retry on timeout — timeout means the agent is too slow, retrying won't help.
   - On HTTPStatusError (5xx only) or httpx.ConnectError → retry
   - On HTTPStatusError (4xx) → return AgentOutcome(status="failed",
       error=AgentError(code="UPSTREAM_ERROR", retryable=False))
       No retry on 4xx — client error means bad input, retrying won't help.

2. Retry (5xx/connection error only):
   - await asyncio.sleep(1.0)
   - Clone the task with attempt=2: task.model_copy(update={"attempt": 2})
   - POST agent_url/tasks/send again
   - On 2xx → return AgentOutcome
   - On any failure → return AgentOutcome(status="failed",
       error=AgentError(code="UPSTREAM_ERROR",
           message=f"Failed after 2 attempts: {error}", retryable=False))
```

**Key constraints:**

- Only retry on **HTTP 5xx** (check `e.response.status_code >= 500`) and **connection errors** (`httpx.ConnectError`, `httpx.ConnectTimeout`)
- Never retry on 4xx — these indicate bad input, not transient failure
- Never retry on general timeout (`httpx.ReadTimeout`, `httpx.WriteTimeout`) — the agent is slow, a second attempt won't be faster
- The 1-second delay is a simple `asyncio.sleep(1.0)` — not exponential backoff
- `attempt` field on the second request must be `2` — agents log this for tracing
- Return the **attempt number that succeeded** in the outcome (for logging by the caller)

**Add a helper property or method:**

`_is_retryable(error: Exception) -> bool`:
- `httpx.HTTPStatusError` with `status_code >= 500` → True
- `httpx.ConnectError` → True
- `httpx.ConnectTimeout` → True
- Everything else → False

> ⚠️ **Two independent retry loops.** This network retry (5xx recovery, increments `attempt`) lives in `TaskDispatcher` and fires for transient infrastructure errors. The LLM validation retry (wrong schema from model, 1 retry) lives inside each agent's `tools.py` and fires for model output errors. They do not interact — a single request can trigger both (network retry to reach the agent, then the agent internally retries its LLM call).

---

### Step 5.2 — Define Use-Case Agent Selection Logic

**File:** `agents/orchestrator/workflow.py` — new file.

**Purpose:** Determines which agents to invoke based on `use_case` and `has_foreign_addr`. Separates routing logic from the main orchestration function for clarity and testability.

**Function to implement:**

`get_agent_plan(use_case: str, has_foreign_addr: bool) -> dict`:

Returns a dict describing which agents to invoke:

```python
{
    "parallel": [
        # Each entry: (skill, agent_name, required)
        # required=True means failure → overall request fails
        # required=False means failure → outcome marked, pipeline continues
    ],
    "sequential": [
        # Always Risk Synthesis as the final step
        ("risk_synthesis", "synthesis", True),
    ],
}
```

**Agent selection rules by use case:**

| Use Case | Equifax | Employment | International | Synthesis |
|---|---|---|---|---|
| `mortgage` + `has_foreign_addr=true` | ✅ required | ✅ required | ✅ optional | ✅ required |
| `mortgage` + `has_foreign_addr=false` | ✅ required | ✅ required | ❌ skipped | ✅ required |
| `rental` | ✅ required | ✅ required | ❌ skipped | ✅ required |
| `auto` | ✅ required | ✅ required | ❌ skipped | ✅ required |
| `hire` + `has_foreign_addr=true` | ❌ skipped | ✅ required | ✅ required | ✅ required |
| `hire` + `has_foreign_addr=false` | ❌ skipped | ✅ required | ✅ optional | ✅ required |

**Return examples:**

UC-1a (`mortgage`, `has_foreign_addr=True`):
```python
{
    "parallel": [
        ("credit_score", "equifax", True),
        ("employment_status", "employment", True),
        ("international_credit_score", "intl", False),
    ],
    "sequential": [("risk_synthesis", "synthesis", True)],
}
```

UC-2 (`rental`, `has_foreign_addr=False`):
```python
{
    "parallel": [
        ("credit_score", "equifax", True),
        ("employment_status", "employment", True),
    ],
    "sequential": [("risk_synthesis", "synthesis", True)],
}
```

UC-4 (`hire`, `has_foreign_addr=True`):
```python
{
    "parallel": [
        ("employment_status", "employment", True),
        ("international_credit_score", "intl", True),
    ],
    "sequential": [("risk_synthesis", "synthesis", True)],
}
```

**`required` flag semantics:**

- `required=True`: If this agent fails (after retry) or times out, the overall verification request status becomes `"failed"`. However, the pipeline still continues to Risk Synthesis with the failure context — synthesis can still produce a low-confidence decision noting the data gap.
- `required=False` (optional): If this agent fails or times out, the pipeline continues normally. The outcome is passed to Risk Synthesis with the failure/timeout context. This is the International agent in mortgage use cases.

> ⚠️ **Skipped agents never appear in the parallel list.** A skipped agent gets an `AgentOutcome(status="skipped", artifact=None, error=None)` injected directly into the outcomes list without any dispatch. This is distinct from an optional agent that is dispatched but fails or returns unavailable.

---

### Step 5.3 — Rewrite `run_verification` for Full Fan-Out and Sequential Chain

**File:** `agents/orchestrator/__init__.py` — replace the Phase 3 `run_verification` function.

**Import additions:** `AgentOutcome` from `agents.shared.a2a_types`; `get_agent_plan` from `.workflow`.

**New `run_verification` logic:**

```
async def run_verification(task_id, correlation_id, body):
    """Full orchestration pipeline: fan-out → collect → chain to synthesis."""

    try:
        task_manager.update_request_status(task_id, "working")

        # 1. Determine which agents to invoke
        plan = get_agent_plan(body.use_case, body.has_foreign_addr)

        # 2. Resolve all parallel agent URLs from Registry
        skills_to_resolve = [skill for skill, _, _ in plan["parallel"]]
        skill_urls = await resolver.find_all(skills_to_resolve)

        # 3. Build and dispatch all parallel tasks concurrently
        outcomes: list[AgentOutcome] = []

        async def dispatch_one(skill, agent_name, required):
            """Dispatch a single agent task and record it in the DB."""
            agent_url = skill_urls[skill]
            agent_task_id = str(uuid.uuid4())
            a2a_task = A2ATask(
                task_id=agent_task_id,
                correlation_id=correlation_id,
                skill=skill,
                input=body.model_dump(),
                attempt=1,
            )
            started_at = datetime.now(timezone.utc).isoformat()
            task_manager.create_agent_task(
                agent_task_id, task_id, correlation_id,
                agent_name, skill, body.model_dump(), 1, started_at,
            )

            outcome = await dispatcher.dispatch(agent_url, a2a_task, agent_name)

            ended_at = datetime.now(timezone.utc).isoformat()
            # Record final attempt number (2 if retried)
            error_dict = outcome.error.model_dump() if outcome.error else None
            task_manager.complete_agent_task(
                agent_task_id, outcome.status,
                outcome.artifact, error_dict, ended_at,
            )
            return outcome

        # 4. Fan-out: asyncio.gather across all parallel agents
        parallel_outcomes = await asyncio.gather(
            *[dispatch_one(skill, name, req) for skill, name, req in plan["parallel"]]
        )
        outcomes.extend(parallel_outcomes)

        # 5. Add skipped agent outcomes
        #    Determine which agents were skipped (not in the parallel plan)
        all_possible = {
            "credit_score": "equifax",
            "employment_status": "employment",
            "international_credit_score": "intl",
        }
        invoked_skills = {skill for skill, _, _ in plan["parallel"]}
        for skill, agent_name in all_possible.items():
            if skill not in invoked_skills:
                # Record a skipped row in agent_tasks
                skipped_task_id = str(uuid.uuid4())
                now_ts = datetime.now(timezone.utc).isoformat()
                task_manager.create_agent_task(
                    skipped_task_id, task_id, correlation_id,
                    agent_name, skill, {}, 0, now_ts,
                )
                task_manager.complete_agent_task(
                    skipped_task_id, "skipped", None, None, now_ts,
                )
                outcomes.append(AgentOutcome(
                    agent_name=agent_name,
                    skill=skill,
                    status="skipped",
                    artifact=None,
                    error=None,
                ))

        # 6. Check if any required agent failed critically
        required_agents = {name for _, name, req in plan["parallel"] if req}
        required_failures = [
            o for o in outcomes
            if o.agent_name in required_agents and o.status in ("failed", "timed_out")
        ]
        # Note: even if required agents failed, we still call synthesis
        # to get a low-confidence decision. The overall request status will
        # be determined by the synthesis result + whether required agents failed.

        # 7. Sequential chain: dispatch to Risk Synthesis
        synthesis_skill, synthesis_name, _ = plan["sequential"][0]
        synthesis_url = await resolver.find(synthesis_skill)

        synthesis_input = {
            "outcomes": [o.model_dump() for o in outcomes],
            "use_case": body.use_case,
        }
        synthesis_task_id = str(uuid.uuid4())
        synthesis_a2a_task = A2ATask(
            task_id=synthesis_task_id,
            correlation_id=correlation_id,
            skill=synthesis_skill,
            input=synthesis_input,
            attempt=1,
        )
        started_at = datetime.now(timezone.utc).isoformat()
        task_manager.create_agent_task(
            synthesis_task_id, task_id, correlation_id,
            synthesis_name, synthesis_skill, synthesis_input, 1, started_at,
        )

        synthesis_outcome = await dispatcher.dispatch(
            synthesis_url, synthesis_a2a_task, synthesis_name
        )

        ended_at = datetime.now(timezone.utc).isoformat()
        error_dict = synthesis_outcome.error.model_dump() if synthesis_outcome.error else None
        task_manager.complete_agent_task(
            synthesis_task_id, synthesis_outcome.status,
            synthesis_outcome.artifact, error_dict, ended_at,
        )

        # 8. Determine final status and decision
        if synthesis_outcome.status == "completed" and synthesis_outcome.artifact:
            decision = synthesis_outcome.artifact.get("decision")
            task_manager.update_request_status(task_id, "completed", decision=decision)
        elif required_failures:
            # Required agent failed AND synthesis couldn't produce a decision
            task_manager.update_request_status(task_id, "failed")
        else:
            task_manager.update_request_status(task_id, "failed")

    except Exception:
        task_manager.update_request_status(task_id, "failed")
```

**Key design decisions:**

1. **Fan-out uses `asyncio.gather`** — all parallel tasks dispatch simultaneously. Total wall-clock time ≈ slowest agent, not sum of all agents.

2. **Skipped agents get `agent_tasks` rows** — status `"skipped"`, `attempt=0`. This ensures `GET /verify/{task_id}/tasks` shows all agents that were considered, not just those dispatched.

3. **Required agent failure does NOT abort the pipeline.** Even if Equifax fails, we still call Risk Synthesis with the failure context. Synthesis can still produce a `"decline"` or `"review"` decision noting the data gap. The overall request status is determined by whether synthesis can produce a decision.

4. **Synthesis receives ALL outcomes** — completed, failed, timed_out, AND skipped. The synthesis prompt explicitly handles each status differently (see Phase 4 prompts).

5. **The decision field** on `verification_requests` comes from the synthesis artifact's `decision` key (`"approve"`, `"review"`, `"decline"`).

---

### Step 5.4 — Update `TaskManager` for the `decision` Field

**File:** `agents/orchestrator/db.py` — update `update_request_status`.

**What changes:** The `update_request_status` method already accepts an optional `decision` parameter. Verify it writes to the `decision` column when provided and sets `completed_at` when status is terminal (`completed` or `failed`).

If the existing implementation does not write `decision`, add:
```sql
UPDATE verification_requests
SET status = ?, decision = ?, completed_at = ?
WHERE id = ?
```

Where `completed_at` is set to `datetime.now(timezone.utc).isoformat()` when status is `"completed"` or `"failed"`.

---

### Step 5.5 — Add a Timeout Simulation Endpoint to a Data Agent (Test Support)

**File:** `agents/intl/main.py` — add a test-support endpoint.

**Purpose:** Phase 5 tests need to verify timeout handling. Rather than making the real LLM call slow, add a `/tasks/send-slow` endpoint that sleeps for a configurable duration before responding.

**Endpoint:**

```
POST /tasks/send-slow:
    Accept A2ATask body + query param ?delay_seconds=35 (default 35)
    await asyncio.sleep(delay_seconds)
    Then proceed as normal (call LLM or return stub)
```

> ⚠️ This endpoint is for **test use only**. It is never called by the Orchestrator in normal operation. The Phase 5 timeout test will call it directly and then separately verify the Orchestrator's timeout logic by using `respx` to mock a slow response. See Step 5.8 for test details.

**Alternative approach (preferred for integration tests):** Instead of the slow endpoint, the Phase 5 timeout test can use environment variable `AGENT_TIMEOUT_SECONDS=2` and then test against the real International agent with a very short timeout on the dispatcher. This approach is simpler and doesn't require a test endpoint.

Choose the approach that works best: either add the slow endpoint, or configure a short timeout in tests. The guide provides both options — the timeout test in Step 5.8 uses the **short-timeout approach** (override `dispatcher.timeout` to 0.001 seconds to force a timeout against a real agent).

---

### Step 5.6 — Rebuild and Basic Smoke Test

```bash
$ docker compose down
$ docker compose up --build
```

Verify the Orchestrator still starts correctly:
```
orchestrator-1  | [Registry Client] Registered successfully — url_hash: ...
```

Quick smoke test before running the full suite:
```bash
# Full verification flow — should invoke Equifax + Employment + Intl + Synthesis
$ curl -s -X POST http://localhost:8000/verify \
    -H "Content-Type: application/json" \
    -d '{"subject_name":"Jane Doe","subject_id":"S001","use_case":"mortgage","has_foreign_addr":true,"consent":true}' \
    | python3 -m json.tool
# Expect: task_id, correlation_id, stream_url

# Poll until completion (replace TASK_ID)
$ curl -s http://localhost:8000/verify/TASK_ID | python3 -m json.tool
# Expect: status "completed", decision is one of approve/review/decline

# Inspect all agent tasks
$ curl -s http://localhost:8000/verify/TASK_ID/tasks | python3 -m json.tool
# Expect: 4 rows — equifax, employment, intl, synthesis — all "completed"
```

---

### Step 5.7 — Verify Use-Case Routing

```bash
# UC-2: rental — International should be skipped
$ curl -s -X POST http://localhost:8000/verify \
    -H "Content-Type: application/json" \
    -d '{"subject_name":"Bob Smith","subject_id":"S002","use_case":"rental","has_foreign_addr":false,"consent":true}' \
    | python3 -m json.tool
# Wait for completion, then:
$ curl -s http://localhost:8000/verify/TASK_ID/tasks | python3 -m json.tool
# Expect: equifax=completed, employment=completed, intl=skipped, synthesis=completed

# UC-4: hire — Equifax should be skipped
$ curl -s -X POST http://localhost:8000/verify \
    -H "Content-Type: application/json" \
    -d '{"subject_name":"Carlos Rivera","subject_id":"S003","use_case":"hire","has_foreign_addr":true,"consent":true}' \
    | python3 -m json.tool
# Wait for completion, then:
$ curl -s http://localhost:8000/verify/TASK_ID/tasks | python3 -m json.tool
# Expect: equifax=skipped, employment=completed, intl=completed, synthesis=completed
```

---

### Step 5.8 — Write Phase 5 Tests

**File:** `tests/test_phase5_orchestration.py`

These are the most critical tests in the project. They verify the orchestration patterns that make the system robust.

**Fixtures** (use existing from `conftest.py`): `orchestrator_client`.

**Helpers:**

```python
import asyncio
import time
import uuid
import httpx
import pytest

VERIFY_URL = "http://localhost:8000"

def _verify_request(use_case="mortgage", has_foreign_addr=True, subject_name="Test Subject"):
    """Build a VerificationRequest body."""
    return {
        "subject_name": subject_name,
        "subject_id": f"S-{uuid.uuid4().hex[:6]}",
        "use_case": use_case,
        "has_foreign_addr": has_foreign_addr,
        "consent": True,
    }

async def _submit_and_wait(client, body, timeout=30):
    """Submit a verify request and poll until terminal status or timeout."""
    resp = await client.post("/verify", json=body)
    assert resp.status_code == 200
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
```

---

**Test cases** (12 tests, all must pass):

---

**1. `test_fan_out_all_agents_mortgage_intl`** — UC-1a full pipeline.

POST `/verify` with `use_case="mortgage"`, `has_foreign_addr=True`. Wait for completion.

Assertions:
- Status is `"completed"`
- `decision` is one of `["approve", "review", "decline"]`
- `GET /verify/{task_id}/tasks` returns 4 rows
- Agent names in tasks: `equifax`, `employment`, `intl`, `synthesis`
- All 4 tasks have `status == "completed"`
- All 4 tasks have the same `correlation_id` matching the one from `POST /verify`

---

**2. `test_fan_out_concurrency`** — Verify fan-out is actually parallel.

POST `/verify` with `use_case="mortgage"`, `has_foreign_addr=True`. Wait for completion. Get all agent tasks.

Assertions:
- All three parallel agents (equifax, employment, intl) have `started_at` timestamps
- The difference between the earliest `started_at` and the latest `started_at` among the three parallel agents is < 2 seconds (they were dispatched nearly simultaneously)
- Total elapsed time (`completed_at - created_at` on the verification_request) is less than the sum of all individual agent durations (proves concurrency, not sequential)

---

**3. `test_optional_agent_skipped_rental`** — UC-2: International skipped entirely.

POST `/verify` with `use_case="rental"`, `has_foreign_addr=False`. Wait for completion.

Assertions:
- Status is `"completed"`
- `GET /verify/{task_id}/tasks` returns 4 rows (equifax, employment, intl, synthesis)
- The `intl` row has `status == "skipped"`
- The `equifax` and `employment` rows have `status == "completed"`
- The `synthesis` row has `status == "completed"`

---

**4. `test_equifax_skipped_hire`** — UC-4: Equifax skipped.

POST `/verify` with `use_case="hire"`, `has_foreign_addr=True`. Wait for completion.

Assertions:
- Status is `"completed"`
- `GET /verify/{task_id}/tasks` shows `equifax` row with `status == "skipped"`
- `employment` and `intl` rows have `status == "completed"`
- `synthesis` has `status == "completed"`

---

**5. `test_intl_unavailable_still_completes`** — UC-1b: International dispatched but returns unavailable.

POST `/verify` with `use_case="mortgage"`, `has_foreign_addr=True`, `subject_name="Domestic Person"`.

The International agent may return `data_availability: "unavailable"` depending on LLM output. Since we can't guarantee the LLM will always return unavailable, this test verifies the structural invariant: even if intl returns unavailable data, the overall pipeline completes.

Assertions:
- Status is `"completed"` (pipeline did not fail)
- The `intl` task row has `status == "completed"` (unavailable is a valid completed response)
- Decision is present

> ⚠️ For a deterministic test of the unavailable path, use `has_foreign_addr=False` with `use_case="mortgage"` and modify the test to confirm intl is skipped. The LLM-driven unavailable path is best tested via the Phase 4 test `test_intl_unavailable_when_no_foreign_addr` which calls the agent directly.

---

**6. `test_correlation_id_flows_through_all_tasks`** — Critical tracing verification.

POST `/verify`. Wait for completion. Get all agent tasks.

Assertions:
- The `correlation_id` from `POST /verify` response matches the `correlation_id` in the verification_request
- Every row in `GET /verify/{task_id}/tasks` has the same `correlation_id`
- No task row has a null or different `correlation_id`

---

**7. `test_synthesis_receives_outcome_bundle`** — Verify synthesis gets the right input.

POST `/verify` with `use_case="mortgage"`, `has_foreign_addr=True`. Wait for completion. Get agent tasks.

Assertions:
- The `synthesis` task row exists and has `status == "completed"`
- The `synthesis` task row's `input` field (JSON) contains `"outcomes"` key (a list)
- The `"outcomes"` list has entries for equifax, employment, and intl
- The `synthesis` task row's `input` contains `"use_case"` key

---

**8. `test_synthesis_decision_stored_on_request`** — Verify decision propagation.

POST `/verify`. Wait for completion.

Assertions:
- `GET /verify/{task_id}` response includes `decision` field
- `decision` is one of `["approve", "review", "decline"]`
- `completed_at` is not null

---

**9. `test_auto_use_case_routes_correctly`** — UC-3: Auto loan.

POST `/verify` with `use_case="auto"`, `has_foreign_addr=False`. Wait for completion.

Assertions:
- Status is `"completed"`
- Agent tasks show: equifax=completed, employment=completed, intl=skipped, synthesis=completed

---

**10. `test_multiple_requests_independent`** — Verify isolation between requests.

Submit two concurrent verification requests with different subjects and use cases. Wait for both.

Assertions:
- Both complete successfully
- Each has a different `correlation_id`
- Each has a different `task_id`
- Agent tasks for request 1 all share correlation_id_1
- Agent tasks for request 2 all share correlation_id_2
- No cross-contamination between the two

---

**11. `test_skipped_agent_task_row_written`** — Verify skipped agents get DB rows.

POST `/verify` with `use_case="rental"`. Wait for completion. Get agent tasks.

Assertions:
- There is a row for `intl` with `status == "skipped"`
- The skipped row has the same `correlation_id` as all other rows
- The skipped row has `attempt == 0` (was never dispatched)

---

**12. `test_full_pipeline_with_weak_profile`** — End-to-end risk assessment quality.

POST `/verify` with `subject_name="Risky McRiskface"`, `use_case="mortgage"`, `has_foreign_addr=True`. Wait for completion.

Assertions:
- Status is `"completed"`
- Decision is present and is one of `["approve", "review", "decline"]`
- `risk_score` is present in the synthesis artifact (check via `/verify/{task_id}/tasks` synthesis row)

> Note: We cannot deterministically force the LLM to generate a weak profile via the subject name alone. This test verifies end-to-end flow completion regardless of the LLM's risk assessment.

---

### Step 5.9 — Run Tests

```bash
$ pip install -r tests/requirements.txt
$ pytest tests/test_phase5_orchestration.py -v
```

All 12 tests must pass. Run the full suite to confirm no regressions:

```bash
$ pytest tests/ -v
```

All Phase 3, Phase 4, and Phase 5 tests must pass.

**Common failure modes:**

| Symptom | Likely Cause |
|---|---|
| `test_fan_out_all_agents_mortgage_intl` times out | `run_verification` exception swallowed — check `GET /verify/{task_id}` for `"failed"` status |
| Only 1 agent_task row (equifax) | Still running old Phase 3 `run_verification` — rebuild containers |
| `intl` row missing | `get_agent_plan` not returning intl for `mortgage` + `has_foreign_addr=True` |
| `synthesis` row has `status="failed"` | Check synthesis agent logs — likely `outcomes` key missing from input |
| `correlation_id` mismatch | Bug in `dispatch_one` — not passing `correlation_id` to task correctly |
| Fan-out test fails (not concurrent) | Using `for` loop + `await` instead of `asyncio.gather` |

---

### Step 5.10 — Manual End-to-End Verification

```bash
# UC-1a: Full four-agent mortgage
$ curl -s -X POST http://localhost:8000/verify \
    -H "Content-Type: application/json" \
    -d '{"subject_name":"Jane Doe","subject_id":"S001","use_case":"mortgage","has_foreign_addr":true,"consent":true}' \
    | python3 -m json.tool
# Copy task_id, poll until completed

$ curl -s http://localhost:8000/verify/TASK_ID | python3 -m json.tool
# Expect: status=completed, decision=approve/review/decline

$ curl -s http://localhost:8000/verify/TASK_ID/tasks | python3 -m json.tool
# Expect: 4 rows (equifax, employment, intl, synthesis) all completed

# UC-2: Rental — intl skipped
$ curl -s -X POST http://localhost:8000/verify \
    -H "Content-Type: application/json" \
    -d '{"subject_name":"Bob Smith","subject_id":"S002","use_case":"rental","has_foreign_addr":false,"consent":true}' \
    | python3 -m json.tool
# Poll, then check tasks:
$ curl -s http://localhost:8000/verify/TASK_ID/tasks | python3 -m json.tool
# Expect: equifax=completed, employment=completed, intl=skipped, synthesis=completed

# UC-4: Hire — equifax skipped
$ curl -s -X POST http://localhost:8000/verify \
    -H "Content-Type: application/json" \
    -d '{"subject_name":"Carlos Rivera","subject_id":"S003","use_case":"hire","has_foreign_addr":true,"consent":true}' \
    | python3 -m json.tool
# Expect: equifax=skipped, employment=completed, intl=completed, synthesis=completed

# Inspect SQLite directly
$ docker compose exec orchestrator python -c "
import sqlite3, json
conn = sqlite3.connect('/data/verifyiq.db')
conn.row_factory = sqlite3.Row

print('=== Recent verification_requests ===')
cur = conn.execute('SELECT id, status, decision, use_case, correlation_id FROM verification_requests ORDER BY created_at DESC LIMIT 3')
for row in cur.fetchall():
    print(dict(row))

print()
print('=== agent_tasks for most recent request ===')
task_id = conn.execute('SELECT id FROM verification_requests ORDER BY created_at DESC LIMIT 1').fetchone()['id']
cur = conn.execute('SELECT agent_name, skill, status, attempt, correlation_id FROM agent_tasks WHERE verification_request_id = ?', (task_id,))
for row in cur.fetchall():
    print(dict(row))
"
```

---

### Step 5.11 — Verify Retry Behavior (Optional Manual Test)

To manually test retry behavior, temporarily stop one agent:

```bash
# Stop the equifax container
$ docker compose stop equifax

# Submit a request that needs equifax
$ curl -s -X POST http://localhost:8000/verify \
    -H "Content-Type: application/json" \
    -d '{"subject_name":"Test Retry","subject_id":"S999","use_case":"mortgage","has_foreign_addr":false,"consent":true}'

# Wait, then check status
$ curl -s http://localhost:8000/verify/TASK_ID | python3 -m json.tool
# Expect: status "failed" (equifax is required, connection error after retry)

# Check orchestrator logs for retry evidence
$ docker compose logs orchestrator 2>&1 | grep -i "retry\|attempt\|failed" | tail -10

# Restart equifax
$ docker compose start equifax
```

---

### Step 5.12 — Commit Phase 5

```bash
$ git add .
$ git commit -m "Phase 5: Full orchestration — fan-out, retry, timeout, optional agents, sequential chain"
```

---

### ✅ Phase 5 Verification Checklist

**Orchestration Flow:**
- [ ] `docker compose up --build` completes without errors after Phase 5 changes
- [ ] `POST /verify` with `use_case="mortgage"`, `has_foreign_addr=true` → status reaches `"completed"` within 30 seconds
- [ ] `GET /verify/{task_id}/tasks` returns exactly 4 rows for a mortgage+intl request
- [ ] All 4 rows share the same `correlation_id`
- [ ] `decision` field on the verification_request is populated (approve/review/decline)

**Use-Case Routing:**
- [ ] `use_case="rental"` → intl row has `status="skipped"`, equifax+employment+synthesis are `"completed"`
- [ ] `use_case="auto"` → same as rental (intl skipped)
- [ ] `use_case="hire"` + `has_foreign_addr=true` → equifax row has `status="skipped"`, employment+intl+synthesis are `"completed"`
- [ ] `use_case="mortgage"` + `has_foreign_addr=false` → intl row has `status="skipped"`

**Fan-Out Concurrency:**
- [ ] Three parallel agent `started_at` timestamps are within 2 seconds of each other (dispatched concurrently, not sequentially)
- [ ] Total pipeline time is less than 3× a single agent call (proves parallelism)

**Retry Logic:**
- [ ] When an agent is down (connection error), orchestrator attempts retry (visible in logs or agent_tasks attempt field)
- [ ] After retry failure, agent_task row shows `status="failed"` with error

**Sequential Chain:**
- [ ] Synthesis agent receives `outcomes` list in its task `input`
- [ ] Outcomes list includes entries for all relevant agents (completed + skipped/failed)
- [ ] Synthesis `input` includes `use_case` field

**Data Integrity:**
- [ ] `correlation_id` is consistent across: verification_request row, all agent_task rows for that request
- [ ] Skipped agent rows have `attempt=0` and `status="skipped"`
- [ ] `completed_at` is populated on the verification_request when status is terminal

**Test Suite:**
- [ ] `pytest tests/test_phase5_orchestration.py -v` — all 12 tests pass
- [ ] `pytest tests/ -v` — all Phase 3 + Phase 4 + Phase 5 tests pass (no regressions)
- [ ] `git log --oneline | head -2` shows Phase 5 commit on top

---

*Document: VerifyIQ Implementation Guide | Phase 5 | Companion to Spec v1.7*
*Next: Phase 6 — LangGraph Employment Agent + SSE Streaming*
