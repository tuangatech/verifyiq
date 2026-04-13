# VerifyIQ — Implementation Guide
## Phases 3 and 4
*Companion to Project Specification v1.6*

---

## How to Use This Guide

This guide describes **what to build and how to verify it** — not the full code. Each step tells you what a file should do, what shape it should take, and what behaviour to expect. Use this as the prompt context for Claude Code to generate the actual file contents.

**Conventions:**

- `$` lines are terminal commands you run directly
- **File description blocks** tell Claude Code what to generate — they describe purpose, structure, key logic, and expected behaviour without containing complete source
- ✅ Checklist items at the end of each phase must all pass before proceeding

---

## Phase 3 — A2A Task Protocol

**Goal:** Full A2A task round-trip working end-to-end with stub artifacts. `correlation_id` visible in all SQLite rows. Protocol verified clean before LLMs are introduced in Phase 4.

**Estimated time:** 3–4 hours

---

### Step 3.1 — Create the Orchestrator Database

**File:** `agents/orchestrator/db.py` — new file.

**Purpose:** `TaskManager` class — wraps all SQLite reads and writes for `verifyiq.db`. Keeps database logic out of route handlers.

**Database path:** Read from `DB_PATH` env var (default `/data/verifyiq.db`).

**`init_db()` method:** Creates three tables if they don't exist:

```sql
CREATE TABLE IF NOT EXISTS verification_requests (
    id               TEXT PRIMARY KEY,   -- also the task_id returned to the client
    correlation_id   TEXT NOT NULL,
    subject_name     TEXT NOT NULL,
    subject_id       TEXT NOT NULL,
    use_case         TEXT NOT NULL,
    has_foreign_addr INTEGER NOT NULL,   -- SQLite boolean: 0 / 1
    status           TEXT NOT NULL DEFAULT 'submitted',
    decision         TEXT,               -- approve / review / decline, populated on completion
    created_at       TEXT NOT NULL,
    completed_at     TEXT
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id                      TEXT PRIMARY KEY,   -- unique per dispatch
    verification_request_id TEXT NOT NULL,
    correlation_id          TEXT NOT NULL,
    agent_name              TEXT NOT NULL,
    skill                   TEXT NOT NULL,
    status                  TEXT NOT NULL,      -- working / completed / failed / timed_out / skipped
    attempt                 INTEGER NOT NULL DEFAULT 1,
    input                   TEXT NOT NULL,      -- JSON
    artifact                TEXT,               -- JSON, null on failure
    error                   TEXT,               -- JSON AgentError, null on success
    started_at              TEXT NOT NULL,
    ended_at                TEXT
);

CREATE TABLE IF NOT EXISTS sse_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id TEXT NOT NULL,
    event_type     TEXT NOT NULL,   -- progress / artifact / completed / error
    payload        TEXT NOT NULL,   -- JSON
    emitted_at     TEXT NOT NULL
);
```

**Methods to implement** (all synchronous, using standard `sqlite3`):

| Method | Description |
|---|---|
| `create_verification_request(task_id, correlation_id, req)` | INSERT into `verification_requests`; `req` is the `VerificationRequest` |
| `update_request_status(task_id, status, decision=None)` | UPDATE status; set `completed_at = now()` when status is terminal |
| `get_verification_request(task_id) -> dict \| None` | SELECT by `id`; return as dict or None |
| `create_agent_task(agent_task_id, verification_request_id, correlation_id, agent_name, skill, input_dict, attempt, started_at)` | INSERT with `status='working'` |
| `complete_agent_task(agent_task_id, status, artifact=None, error=None, ended_at=None)` | UPDATE status, artifact, error, ended_at |
| `get_agent_tasks(verification_request_id) -> list[dict]` | SELECT all task rows for a given request |

Use `json.dumps()` / `json.loads()` for artifact, error, and input fields. Follow the same synchronous `sqlite3` pattern used in the Registry.

---

### Step 3.2 — Wire `verifyiq.db` Into the Orchestrator Lifespan

**File:** `agents/orchestrator/__init__.py` — update the existing lifespan.

**What changes:**

- Import `TaskManager` from `.db`
- Instantiate `task_manager = TaskManager()` at module level alongside `resolver`
- In `lifespan`, call `task_manager.init_db()` before `yield`

Lifespan order: `init_db()` → `register_with_registry()` → *(app runs)* → `deregister_from_registry()`.

Also remove `aiosqlite` from `agents/orchestrator/requirements.txt` — the Orchestrator uses synchronous `sqlite3`, consistent with the Registry.

> ⚠️ `task_manager.init_db()` must complete before the app starts serving requests. The lifespan guarantees this.

---

### Step 3.3 — Add Orchestrator-Specific Models

**File:** `agents/orchestrator/models.py` — new file.

**Purpose:** HTTP boundary models for the Orchestrator's own endpoints. Kept local to the Orchestrator package — these are not inter-agent types and do not belong in `agents/shared/`.

**Models to define:**

`VerificationRequest` — input to `POST /verify`:

| Field | Type | Notes |
|---|---|---|
| `subject_name` | `str` | |
| `subject_id` | `str` | Simulated ID — no real SSNs |
| `use_case` | `Literal["mortgage", "rental", "auto", "hire"]` | |
| `has_foreign_addr` | `bool` | Drives whether Intl Agent is invoked |
| `consent` | `bool` | UI consent checkbox |

`VerifyResponse` — response from `POST /verify`:

| Field | Type | Notes |
|---|---|---|
| `task_id` | `str` | Returned immediately; use to poll status |
| `correlation_id` | `str` | Flows into every downstream task and log row |
| `stream_url` | `str` | e.g. `"/verify/{task_id}/stream"` — active in Phase 6 |

`TaskStatusResponse` — response from `GET /verify/{task_id}`:

| Field | Type | Notes |
|---|---|---|
| `task_id` | `str` | |
| `correlation_id` | `str` | |
| `status` | `Literal["submitted", "working", "completed", "failed"]` | |
| `decision` | `Optional[str]` | Populated on completion |
| `created_at` | `str` | ISO datetime |
| `completed_at` | `Optional[str]` | ISO datetime |

---

### Step 3.4 — Implement `POST /tasks/send` and `GET /tasks/{task_id}` on All Four Data Agents

Add two endpoints and an in-memory task store to each data agent. Stub artifacts are hardcoded intentionally — they verify the full protocol round-trip before LLM complexity is added in Phase 4.

**Pattern for each agent's `main.py`:**

```
Add at module level:
    task_store: dict[str, dict] = {}

POST /tasks/send:
    Accept A2ATask body (from agents.shared.a2a_types)
    started_at = datetime.now(timezone.utc).isoformat()
    artifact = <stub dict for this agent — see below>
    ended_at = datetime.now(timezone.utc).isoformat()
    result = A2ATaskResult(
        task_id        = task.task_id,
        correlation_id = task.correlation_id,   ← echo unchanged
        status         = "completed",
        artifact       = artifact,
        error          = None,
        started_at     = started_at,
        ended_at       = ended_at,
    )
    task_store[task.task_id] = result.model_dump()
    return result

GET /tasks/{task_id}:
    If task_id not in task_store: raise HTTPException(404, "Task not found")
    return task_store[task_id]
```

**Stub artifacts:**

*Equifax* — `skill: credit_score`:
```
source: "equifax"
subject_id: task.input.get("subject_id", "stub")
credit_score: 720
score_model: "FICO 8"
open_accounts: 5
credit_utilization_pct: 32.0
derogatory_marks: 0
hard_inquiries_12mo: 2
oldest_account_years: 7.5
data_as_of: "2026-04-12"
```

*Employment* — `skill: employment_status`:
```
source: "workforce_solutions"
subject_id: task.input.get("subject_id", "stub")
currently_employed: true
employer_name: "Stub Corporation Inc."
employment_start_date: "2020-01-15"
tenure_years: 6.2
employment_type: "full_time"
verified_annual_income: 85000
income_currency: "USD"
verification_confidence: "high"
data_as_of: "2026-04-12"
```

*International* — `skill: international_credit_score`:
```
source: "experian_international"
subject_id: task.input.get("subject_id", "stub")
data_availability: "full"
country_of_record: "CA"
local_credit_score: 750
us_equivalent_score: 710
foreign_tradelines: 3
country_risk_tier: "low"
data_as_of: "2026-04-12"
```

*Risk Synthesis* — `skill: risk_synthesis`:
```
decision: "approve"
confidence: "high"
risk_score: 22
decision_factors: ["Strong credit score (720)", "Stable employment", "Low utilization (32%)"]
risk_flags: []
international_note: null
recommended_actions: []
reasoning_summary: "Stub synthesis — Phase 3. Replace with LLM in Phase 4."
```

> ⚠️ **`correlation_id` must be echoed back unchanged.** Copy from `task.correlation_id` — never regenerate it. The protocol test explicitly verifies the round-trip value.

---

### Step 3.5 — Add `TaskDispatcher` to the Orchestrator

**File:** `agents/orchestrator/dispatcher.py` — new file.

**Purpose:** Sends a single outbound A2A task to a remote agent and returns an `AgentOutcome`. Phase 3 is a simple HTTP POST — retry logic and timeout enforcement are added in Phase 5.

**`TaskDispatcher` class:**

`async dispatch(agent_url: str, task: A2ATask, agent_name: str) -> AgentOutcome`:
- POST `{agent_url}/tasks/send` with the task JSON body; `httpx.AsyncClient` with 35-second timeout (slightly above `AGENT_TIMEOUT_SECONDS` so the orchestrator timeout can fire first in Phase 5)
- On 2xx: parse as `A2ATaskResult`; return `AgentOutcome(agent_name=agent_name, skill=task.skill, status=result.status, artifact=result.artifact, error=result.error)`
- On `httpx.HTTPStatusError` (4xx/5xx): return `AgentOutcome(agent_name=agent_name, skill=task.skill, status="failed", error=AgentError(code="UPSTREAM_ERROR", message=str(e), retryable=True))`
- On `httpx.TimeoutException`: return `AgentOutcome(agent_name=agent_name, skill=task.skill, status="timed_out", error=AgentError(code="TIMEOUT", message="Agent did not respond within timeout", retryable=True))`
- On any other exception: return `AgentOutcome(status="failed", error=AgentError(code="UPSTREAM_ERROR", message=str(e), retryable=False))`

> ⚠️ **No retry in Phase 3.** The one-retry-on-5xx logic (incrementing `attempt` to 2) is added in Phase 5 when the full orchestration patterns are implemented.

---

### Step 3.6 — Wire `POST /verify` on the Orchestrator

**File:** `agents/orchestrator/__init__.py` — add two routes and one background function.

**Imports to add:** `uuid`, `asyncio`, `datetime/timezone` from stdlib; `TaskDispatcher` from `.dispatcher`; `VerificationRequest`, `VerifyResponse`, `TaskStatusResponse` from `.models`.

---

**`POST /verify`:**
```
Accept VerificationRequest body
task_id = str(uuid.uuid4())
correlation_id = str(uuid.uuid4())
task_manager.create_verification_request(task_id, correlation_id, body)
asyncio.create_task(run_verification(task_id, correlation_id, body))
return VerifyResponse(task_id=task_id, correlation_id=correlation_id,
                      stream_url=f"/verify/{task_id}/stream")
```

Return immediately — do not await `run_verification`.

---

**`GET /verify/{task_id}`:**
```
result = task_manager.get_verification_request(task_id)
if result is None: raise HTTPException(404, "Task not found")
return TaskStatusResponse(**result)
```

---

**`GET /verify/{task_id}/tasks`** — debug endpoint (analogous to `GET /resolve/{skill}`):
```
return task_manager.get_agent_tasks(task_id)
```

Useful for the Phase 3 test that verifies `agent_tasks` rows are written with the correct `correlation_id`.

---

**`run_verification(task_id, correlation_id, body)` — async background function** (not a route):

Phase 3 scope: one Equifax round-trip only. Full fan-out to all agents is Phase 5.

```
Wrap everything in try/except — on any exception call
    task_manager.update_request_status(task_id, "failed")
and return.

1. task_manager.update_request_status(task_id, "working")

2. equifax_url = await resolver.find("credit_score")

3. agent_task_id = str(uuid.uuid4())
   a2a_task = A2ATask(
       task_id        = agent_task_id,
       correlation_id = correlation_id,
       skill          = "credit_score",
       input          = body.model_dump(),
       attempt        = 1,
   )

4. started_at = datetime.now(timezone.utc).isoformat()
   task_manager.create_agent_task(
       agent_task_id, task_id, correlation_id,
       "equifax", "credit_score", body.model_dump(), 1, started_at
   )

5. outcome = await dispatcher.dispatch(equifax_url, a2a_task, "equifax")

6. ended_at = datetime.now(timezone.utc).isoformat()
   task_manager.complete_agent_task(
       agent_task_id, outcome.status,
       outcome.artifact, outcome.error, ended_at
   )

7. task_manager.update_request_status(task_id, "completed")
```

Instantiate `dispatcher = TaskDispatcher()` at module level alongside `resolver` and `task_manager`.

---

### Step 3.7 — Rebuild and Start

```bash
$ docker compose down
$ docker compose up --build
```

Watch for both log lines on the Orchestrator:
```
verifyiq-orchestrator-1 | [Registry] Database initialised at /data/verifyiq.db
verifyiq-orchestrator-1 | [Registry Client] Registered successfully — url_hash: ...
```

---

### Step 3.8 — Write Phase 3 Tests

**File:** `tests/test_phase3_a2a.py`

Add fixtures for the four data agents to `tests/conftest.py`:

```python
@pytest_asyncio.fixture
async def equifax_client():
    async with httpx.AsyncClient(base_url="http://localhost:8001", timeout=15.0) as c:
        yield c

# Repeat for employment_client (:8002), intl_client (:8003), synthesis_client (:8004)
```

**Helper** (module-level):
```python
import uuid

def _task(skill: str, subject_id: str = "test-S001", extra_input: dict | None = None) -> dict:
    return {
        "task_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "skill": skill,
        "input": {"subject_id": subject_id, "subject_name": "Test Subject",
                  "use_case": "mortgage", "has_foreign_addr": True, **(extra_input or {})},
        "timeout_ms": 30000,
        "attempt": 1,
    }
```

**Test cases** (one function per case, all 10 must pass):

1. **`test_equifax_task_send_returns_completed`** — POST `_task("credit_score")` to equifax. Assert: `status == "completed"`, `task_id` matches sent value, `correlation_id` matches sent value, `artifact` is not None, `started_at` and `ended_at` are both present.

2. **`test_employment_task_send_returns_completed`** — Same shape, POST to employment:8002, skill `"employment_status"`.

3. **`test_intl_task_send_returns_completed`** — POST to intl:8003, skill `"international_credit_score"`.

4. **`test_synthesis_task_send_returns_completed`** — POST to synthesis:8004, skill `"risk_synthesis"`. Input must include `"outcomes": []` (empty list is fine for stub). Assert `artifact["decision"]` is one of `["approve", "review", "decline"]`.

5. **`test_correlation_id_echoed_back`** — Construct a task with `correlation_id = "corr-phase3-echo-test"`. POST to Equifax. Assert `response["correlation_id"] == "corr-phase3-echo-test"`.

6. **`test_get_task_after_completion`** — POST to Equifax, capture `task_id` from response. Then `GET /tasks/{task_id}`. Assert status code 200, `status == "completed"`, `ended_at` is not None.

7. **`test_get_unknown_task_returns_404`** — `GET http://localhost:8001/tasks/no-such-task-abc123`. Assert status code 404.

8. **`test_verify_returns_task_id`** — POST to `http://localhost:8000/verify` with a valid `VerificationRequest` body. Assert response has non-empty `task_id` and `correlation_id`. Assert `stream_url` is present.

9. **`test_verify_task_completes`** — POST `/verify`, capture `task_id`. Poll `GET /verify/{task_id}` every 500 ms for up to 10 seconds. Assert `status` reaches `"completed"`.

10. **`test_agent_task_written_to_db`** — After `test_verify_task_completes` (or inline): POST `/verify`, wait for completion, then `GET /verify/{task_id}/tasks`. Assert the list contains exactly 1 row, its `agent_name == "equifax"`, its `status == "completed"`, and its `correlation_id` matches the one returned by `POST /verify`.

---

### Step 3.9 — Install Dependencies and Run Tests

```bash
$ pip install pytest pytest-asyncio httpx
$ pytest tests/test_phase3_a2a.py -v
```

All 10 tests must pass. If `test_verify_task_completes` times out, inspect:

```bash
$ docker compose logs orchestrator 2>&1 | tail -30
```

The most common cause is an exception in `run_verification` being swallowed by the try/except and silently setting status to `"failed"` — check whether `GET /verify/{task_id}` returns `"failed"` rather than timing out.

---

### Step 3.10 — Manual Verification

```bash
# Direct agent round-trip
$ curl -s -X POST http://localhost:8001/tasks/send \
    -H "Content-Type: application/json" \
    -d '{"task_id":"m001","correlation_id":"corr-m001","skill":"credit_score","input":{"subject_id":"S001","subject_name":"Jane Doe","use_case":"mortgage","has_foreign_addr":true},"timeout_ms":30000,"attempt":1}' \
    | python3 -m json.tool
# Expect: status "completed", artifact.credit_score 720, correlation_id "corr-m001"

# GET task by ID
$ curl -s http://localhost:8001/tasks/m001 | python3 -m json.tool

# Unknown task → 404
$ curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/tasks/no-such-task
# Expect: 404

# Submit a verification request
$ curl -s -X POST http://localhost:8000/verify \
    -H "Content-Type: application/json" \
    -d '{"subject_name":"Jane Doe","subject_id":"S001","use_case":"mortgage","has_foreign_addr":true,"consent":true}' \
    | python3 -m json.tool
# Copy task_id from response

# Poll for completion (replace TASK_ID)
$ curl -s http://localhost:8000/verify/TASK_ID | python3 -m json.tool
# Expect: status "completed"

# Inspect agent task rows (replace TASK_ID)
$ curl -s http://localhost:8000/verify/TASK_ID/tasks | python3 -m json.tool
# Expect: 1 row, agent_name "equifax", correlation_id matching the one from POST /verify

# Inspect SQLite directly
$ docker compose exec orchestrator sqlite3 /data/verifyiq.db \
    "SELECT id, status, correlation_id, created_at, completed_at FROM verification_requests;"
$ docker compose exec orchestrator sqlite3 /data/verifyiq.db \
    "SELECT id, agent_name, status, correlation_id, started_at, ended_at FROM agent_tasks;"
```

---

### Step 3.11 — Commit Phase 3

```bash
$ git add .
$ git commit -m "Phase 3: A2A task protocol, stub artifacts, TaskManager, POST /verify"
```

---

### ✅ Phase 3 Verification Checklist

- [ ] `docker compose up --build` completes without errors after Phase 3 changes
- [ ] Orchestrator startup logs show `Database initialised at /data/verifyiq.db`
- [ ] `curl -s -X POST http://localhost:8001/tasks/send -H "Content-Type: application/json" -d '{"task_id":"c1","correlation_id":"r1","skill":"credit_score","input":{"subject_id":"S1"},"timeout_ms":30000,"attempt":1}'` returns `"status": "completed"` and `"correlation_id": "r1"`
- [ ] Response includes non-null `artifact`, `started_at`, and `ended_at`
- [ ] `curl -s http://localhost:8001/tasks/c1` returns the same result (GET retrieves stored task)
- [ ] `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/tasks/no-such-task` prints `404`
- [ ] Same POST/GET/404 checks pass for employment:8002, intl:8003, synthesis:8004
- [ ] `curl -s -X POST http://localhost:8000/verify ...` returns JSON with `task_id`, `correlation_id`, `stream_url`
- [ ] Polling `GET /verify/{task_id}` reaches `"status": "completed"` within 5 seconds
- [ ] `GET /verify/{task_id}/tasks` returns 1 row with `agent_name: "equifax"` and non-empty `correlation_id`
- [ ] `correlation_id` in `verification_requests` SQLite row matches `correlation_id` in the `agent_tasks` row
- [ ] `docker compose exec orchestrator sqlite3 /data/verifyiq.db "SELECT count(*) FROM agent_tasks;"` returns ≥ 1
- [ ] `pytest tests/test_phase3_a2a.py -v` — all 10 tests pass
- [ ] `git log --oneline | head -2` shows Phase 3 commit on top

---

## Phase 4 — LLM Artifact Generation

**Goal:** Each data agent produces a realistic, schema-valid, varied artifact on every call. OpenRouter calls working. Pydantic validation and one-retry-on-failure confirmed.

**Estimated time:** 2–3 hours

---

### Step 4.1 — Understand the Shared LLM Call Pattern

Before writing any agent code, understand the pattern all four agents follow. There is no shared LLM helper — each agent implements it in its own `tools.py` (agent independence > DRY). The pattern is identical in structure:

```
1. Build system prompt — persona + exact JSON schema the model must emit
2. Build user prompt — subject seed (name, ID, use case, flags)
3. POST to OpenRouter:
       URL:     https://openrouter.ai/api/v1/chat/completions
       Headers: Authorization: Bearer {OPENROUTER_API_KEY}
       Body:    {
                  "model": <from env>,
                  "messages": [
                      {"role": "system", "content": <system_prompt>},
                      {"role": "user",   "content": <user_prompt>}
                  ],
                  "response_format": {"type": "json_object"},
                  "temperature": 0.7   (0.2 for Risk Synthesis)
                }
4. Extract: response.json()["choices"][0]["message"]["content"]
5. json.loads() → validate against Pydantic model
6. On ValidationError or JSONDecodeError:
       Append error to user prompt and retry once
7. On second failure:
       Return AgentError(code="LLM_VALIDATION_FAILED", retryable=False)
```

Read `OPENROUTER_API_KEY` and model name from environment at call time. Never hardcode model strings.

> ⚠️ **Two independent retry loops.** The LLM validation retry (wrong schema from model, 1 retry) lives inside each agent's tools and is implemented in this phase. The network retry (5xx recovery, increments `attempt`) lives in `TaskDispatcher` and is added in Phase 5. They do not interact.

> ⚠️ **Employment Agent in Phase 4 is a single-node stub.** The multi-step LangGraph graph with conditional branching (`lookup_current_employer → lookup_prior_employer` on miss) is added in Phase 6. In Phase 4, one LLM call generates the full `EmploymentArtifact`. The Orchestrator and Registry see no difference — same endpoint, same A2A protocol.

---

### Step 4.2 — Implement the Equifax Agent LLM Toolchain

**File:** `agents/equifax/prompts.py` — replace the Phase 2 stub.

**`SYSTEM_PROMPT`** constant: Tell the model it is a simulated Equifax credit data API. Instruct it to generate a realistic FICO 8 credit profile reflecting the subject's demographics and use case. Emphasise: output **only** a single valid JSON object matching this exact schema — no explanation, no markdown, no extra keys:

```json
{
  "source": "equifax",
  "subject_id": "<string>",
  "credit_score": <integer 300–850>,
  "score_model": "FICO 8",
  "open_accounts": <integer ≥ 0>,
  "credit_utilization_pct": <float 0.0–100.0>,
  "derogatory_marks": <integer ≥ 0>,
  "hard_inquiries_12mo": <integer ≥ 0>,
  "oldest_account_years": <float ≥ 0>,
  "data_as_of": "<YYYY-MM-DD>"
}
```

**`build_user_prompt(subject_id, subject_name, use_case, today) -> str`**: Format a user prompt string including all four values. Hint the model to vary the profile by use case — a mortgage applicant may have a stronger profile than a subprime rental applicant.

---

**File:** `agents/equifax/tools.py` — replace the Phase 2 stub.

**Functions to implement:**

`build_persona_seed(task_input: dict) -> dict`
- Extract `subject_id`, `subject_name`, `use_case` from task input
- Add `today = date.today().isoformat()`
- Return as a clean dict

`async call_llm_credit_profile(seed: dict) -> str`
- Build prompts from `prompts.py`
- POST to OpenRouter with `os.environ["OPENROUTER_API_KEY"]` and `os.environ["OPENROUTER_AGENT_MODEL"]`
- Use `httpx.AsyncClient(timeout=30.0)`
- Raise `httpx.HTTPStatusError` on non-2xx
- Return `response.json()["choices"][0]["message"]["content"]`

`validate_credit_artifact(raw: str) -> EquifaxArtifact`
- `json.loads(raw)` then `EquifaxArtifact(**data)`
- Let `ValidationError` and `JSONDecodeError` propagate — caller handles retry

`async retry_with_correction(raw: str, error: str, seed: dict) -> str`
- Rebuild user prompt with the validation error appended: `"Your previous output failed validation: {error}. Correct and return only valid JSON."`
- Make a second LLM call with the same system prompt
- Return raw content string — do not validate inside this function

---

**File:** `agents/equifax/main.py` — replace the stub artifact in `POST /tasks/send`.

Updated handler logic:
```
started_at = now()
seed = build_persona_seed(task.input)

raw = await call_llm_credit_profile(seed)
try:
    artifact = validate_credit_artifact(raw)
except (ValidationError, JSONDecodeError) as e:
    raw = await retry_with_correction(raw, str(e), seed)
    try:
        artifact = validate_credit_artifact(raw)
    except Exception as e2:
        ended_at = now()
        result = A2ATaskResult(
            task_id=task.task_id, correlation_id=task.correlation_id,
            status="failed", artifact=None,
            error=AgentError(code="LLM_VALIDATION_FAILED", message=str(e2), retryable=False),
            started_at=started_at, ended_at=ended_at,
        )
        task_store[task.task_id] = result.model_dump()
        return result

ended_at = now()
result = A2ATaskResult(
    task_id=task.task_id, correlation_id=task.correlation_id,
    status="completed", artifact=artifact.model_dump(),
    error=None, started_at=started_at, ended_at=ended_at,
)
task_store[task.task_id] = result.model_dump()
return result
```

---

### Step 4.3 — Implement the International Agent LLM Toolchain

**File:** `agents/intl/prompts.py` — replace stub.

**`SYSTEM_PROMPT`**: Simulated Experian International API persona. Output only:
```json
{
  "source": "experian_international",
  "subject_id": "<string>",
  "data_availability": "<full | partial | unavailable>",
  "reason": "<string or null>",
  "country_of_record": "<ISO 3166-1 alpha-2 or null>",
  "local_credit_score": <integer or null>,
  "us_equivalent_score": <integer or null>,
  "foreign_tradelines": <integer or null>,
  "country_risk_tier": "<low | moderate | high | very_high | null>",
  "data_as_of": "<YYYY-MM-DD or null>"
}
```

**Key instruction to include in system prompt:** If `has_foreign_addr` is `false` or the subject has no foreign address history, set `data_availability` to `"unavailable"`, populate `reason` with a brief explanation, and set all other optional fields to `null`. This path is tested directly by UC-1b.

**`build_user_prompt(subject_id, subject_name, has_foreign_addr, today) -> str`**: Include `has_foreign_addr` explicitly so the model receives the signal.

---

**File:** `agents/intl/tools.py` — replace stub.

**Functions to implement:**

`build_intl_persona_seed(task_input: dict) -> dict`
- Extract `subject_id`, `subject_name`, `has_foreign_addr` (default `False`), `country_of_origin` (optional)

`async call_llm_intl_profile(seed: dict) -> str`
- Same pattern as Equifax; uses `OPENROUTER_AGENT_MODEL`

`validate_intl_artifact(raw: str) -> InternationalArtifact`

`async retry_with_correction(raw: str, error: str, seed: dict) -> str`

---

**File:** `agents/intl/main.py` — replace stub artifact with LLM toolchain call. Same handler logic as Equifax.

`data_availability: "unavailable"` is a valid **completed** result — not a failure. Return it as `A2ATaskResult(status="completed", artifact=artifact.model_dump())`. The Orchestrator handles the null artifact path.

---

### Step 4.4 — Implement the Employment Agent (Single-Node LLM Stub)

**File:** `agents/employment/prompts.py` — replace stub.

**`SYSTEM_PROMPT`**: Simulated Equifax Workforce Solutions / The Work Number API. Output only:
```json
{
  "source": "workforce_solutions",
  "subject_id": "<string>",
  "currently_employed": <boolean>,
  "employer_name": "<string or null>",
  "employment_start_date": "<YYYY-MM-DD or null>",
  "tenure_years": <float or null>,
  "employment_type": "<full_time | part_time | contract | self_employed | null>",
  "verified_annual_income": <integer or null>,
  "income_currency": "USD",
  "verification_confidence": "<high | medium | low>",
  "data_as_of": "<YYYY-MM-DD>"
}
```

Instruct the model: if `currently_employed` is false, the optional employer fields may reflect prior employment or be null.

**`build_user_prompt(subject_id, subject_name, use_case, today) -> str`**

---

**File:** `agents/employment/tools.py` — replace stub.

**Functions:** `build_employment_seed`, `async call_llm_employment_profile`, `validate_employment_artifact`, `async retry_with_correction` — same structure as Equifax.

---

**File:** `agents/employment/main.py` — replace stub with LLM toolchain. Same handler logic.

> ⚠️ In Phase 6 this entire handler is replaced by a LangGraph graph. The single-call stub here produces the same `EmploymentArtifact` schema, so Phase 3/4/5 work correctly until then.

---

### Step 4.5 — Implement the Risk Synthesis Agent LLM Toolchain

**File:** `agents/synthesis/prompts.py` — replace stub.

**`SYSTEM_PROMPT`**: Senior credit risk analyst persona. Output only:
```json
{
  "decision": "<approve | review | decline>",
  "confidence": "<high | medium | low>",
  "risk_score": <integer 0–100>,
  "decision_factors": ["<string>", ...],
  "risk_flags": ["<string>", ...],
  "international_note": "<string or null>",
  "recommended_actions": ["<string>", ...],
  "reasoning_summary": "<string>"
}
```

Include explicit reasoning instructions for each outcome status:
- `completed` — use the artifact data
- `timed_out` — treat as a transient infrastructure issue; reduce confidence; do not penalise the subject
- `failed` — treat as a data gap; note it in `risk_flags`; reduce confidence
- `skipped` — agent was not invoked for this use case; do not mention it

Use-case weighting guidance (include in system prompt):
- `mortgage` — weight all three data sources equally
- `auto` / `rental` — weight employment stability and debt-to-income most heavily
- `hire` — weight employment history and international profile most heavily

**`build_synthesis_prompt(outcomes: list[dict], use_case: str) -> str`**: Format each outcome into a structured block the LLM can read. Group by status — completed artifacts first, then failure context.

---

**File:** `agents/synthesis/tools.py` — replace stub.

**Functions to implement:**

`parse_outcome_bundle(task_input: dict) -> tuple[list[dict], str]`
- Extract `outcomes` (list) and `use_case` (str) from task input
- Raise `ValueError` if either key is missing

`async call_llm_decision(outcomes: list[dict], use_case: str) -> str`
- Uses `OPENROUTER_SYNTHESIS_MODEL` (not `OPENROUTER_AGENT_MODEL`)
- Temperature **0.2** — lower than data agents for more consistent risk decisions

`validate_decision_artifact(raw: str) -> VerificationDecision`

`async retry_with_correction(raw: str, error: str, outcomes: list[dict], use_case: str) -> str`

---

**File:** `agents/synthesis/main.py` — replace stub with LLM toolchain.

The task `input` is expected to contain:
```json
{
  "outcomes": [ ...list of AgentOutcome-shaped dicts... ],
  "use_case": "mortgage"
}
```

Updated handler:
```
started_at = now()
try:
    outcomes, use_case = parse_outcome_bundle(task.input)
except ValueError as e:
    return A2ATaskResult(status="failed",
        error=AgentError(code="INVALID_INPUT", message=str(e), retryable=False), ...)

raw = await call_llm_decision(outcomes, use_case)
try:
    artifact = validate_decision_artifact(raw)
except Exception as e:
    raw = await retry_with_correction(raw, str(e), outcomes, use_case)
    try:
        artifact = validate_decision_artifact(raw)
    except Exception as e2:
        return A2ATaskResult(status="failed",
            error=AgentError(code="LLM_VALIDATION_FAILED", message=str(e2), retryable=False), ...)

return A2ATaskResult(status="completed", artifact=artifact.model_dump(), ...)
```

---

### Step 4.6 — Rebuild and Start

```bash
$ docker compose down
$ docker compose up --build
```

Verify env vars are reaching the containers before testing:

```bash
$ docker compose exec equifax env | grep OPENROUTER
# Expect: OPENROUTER_API_KEY and OPENROUTER_AGENT_MODEL both printed

$ docker compose exec synthesis env | grep OPENROUTER
# Expect: OPENROUTER_API_KEY and OPENROUTER_SYNTHESIS_MODEL both printed
```

If either is missing, check the relevant service's `environment:` block in `docker-compose.yml` against the `.env` variable names.

---

### Step 4.7 — Write Phase 4 Tests

**File:** `tests/test_phase4_llm.py`

All 8 tests are HTTP integration tests against the live Docker stack with a live OpenRouter API key. No mocking is required — the key behaviour to verify is schema validity and output variation, not internal retry mechanics.

**Helpers:**
```python
from agents.shared.schemas import (
    EquifaxArtifact, EmploymentArtifact, InternationalArtifact, VerificationDecision
)

def _task(skill, input_dict):
    return {"task_id": str(uuid.uuid4()), "correlation_id": str(uuid.uuid4()),
            "skill": skill, "input": input_dict, "timeout_ms": 30000, "attempt": 1}
```

**Test cases:**

1. **`test_equifax_produces_valid_artifact`** — POST `_task("credit_score", {...})` to equifax:8001. Parse `response["artifact"]` as `EquifaxArtifact`. Assert `300 ≤ credit_score ≤ 850` and `source == "equifax"`.

2. **`test_equifax_output_varies`** — Call Equifax three times with subjects `"Alice Smith"`, `"Bob Jones"`, `"Carlos Rivera"`. Collect the three `credit_score` values. Assert `len(set(scores)) > 1` (at least 2 distinct values — LLM is not returning a static response).

3. **`test_employment_produces_valid_artifact`** — POST to employment:8002. Parse as `EmploymentArtifact`. Assert `verification_confidence` is one of `["high", "medium", "low"]` and `source == "workforce_solutions"`.

4. **`test_intl_full_when_foreign_addr_true`** — POST with `has_foreign_addr: true`. Parse as `InternationalArtifact`. Assert `data_availability in ["full", "partial"]`.

5. **`test_intl_unavailable_when_no_foreign_addr`** — POST with `has_foreign_addr: false`. Parse as `InternationalArtifact`. Assert `data_availability == "unavailable"` and `reason` is not None.

6. **`test_synthesis_produces_valid_decision`** — POST to synthesis:8004 with a hardcoded outcome bundle (equifax + employment + intl, all `completed` with valid artifact dicts). Parse `artifact` as `VerificationDecision`. Assert `decision in ["approve", "review", "decline"]` and `0 ≤ risk_score ≤ 100`.

7. **`test_synthesis_declines_on_weak_profile`** — POST to Synthesis with a bundle containing: `credit_score: 440`, `derogatory_marks: 5`, `credit_utilization_pct: 95`, `currently_employed: false`, `verification_confidence: "low"`. Assert `decision != "approve"`.

8. **`test_synthesis_handles_timed_out_outcome`** — POST to Synthesis with the Intl outcome having `status: "timed_out"`, `artifact: null`, `error: {"code": "TIMEOUT", "message": "...", "retryable": true}`. Assert `response["status"] == "completed"` (synthesis still runs) and `artifact["confidence"] in ["medium", "low"]`.

> ⚠️ Tests 1–8 make live OpenRouter API calls and consume credit. Each call costs fractions of a cent on `gemini-3-flash` / `gpt-5.4-mini`. The full suite costs well under $0.05. If you want to skip live costs during iteration, run only tests 6–8 (Synthesis is cheapest) and eyeball Equifax/Employment/Intl manually.

---

### Step 4.8 — Run Tests

```bash
$ pytest tests/test_phase4_llm.py -v
```

All 8 tests must pass. If a test fails due to an OpenRouter timeout or 429 rate limit, re-run — transient failures at this stage are infrastructure, not code bugs.

Run the full suite to confirm Phase 3 tests still pass:
```bash
$ pytest tests/ -v
```

---

### Step 4.9 — Manual Verification

```bash
# Equifax — real LLM output
$ curl -s -X POST http://localhost:8001/tasks/send \
    -H "Content-Type: application/json" \
    -d '{"task_id":"llm-001","correlation_id":"c-llm-001","skill":"credit_score","input":{"subject_id":"S001","subject_name":"Jane Doe","use_case":"mortgage","has_foreign_addr":true},"timeout_ms":30000,"attempt":1}' \
    | python3 -m json.tool
# Expect: credit_score varies from the 720 stub; artifact matches EquifaxArtifact schema

# International — unavailable path
$ curl -s -X POST http://localhost:8003/tasks/send \
    -H "Content-Type: application/json" \
    -d '{"task_id":"llm-002","correlation_id":"c-llm-002","skill":"international_credit_score","input":{"subject_id":"S002","subject_name":"Tom Brown","use_case":"rental","has_foreign_addr":false},"timeout_ms":30000,"attempt":1}' \
    | python3 -m json.tool
# Expect: artifact.data_availability = "unavailable", reason populated

# Synthesis — stress test with a bad profile
$ curl -s -X POST http://localhost:8004/tasks/send \
    -H "Content-Type: application/json" \
    -d '{"task_id":"llm-003","correlation_id":"c-llm-003","skill":"risk_synthesis","input":{"use_case":"mortgage","outcomes":[{"agent_name":"equifax","skill":"credit_score","status":"completed","artifact":{"source":"equifax","subject_id":"S003","credit_score":490,"score_model":"FICO 8","open_accounts":14,"credit_utilization_pct":91.0,"derogatory_marks":4,"hard_inquiries_12mo":8,"oldest_account_years":1.5,"data_as_of":"2026-04-12"},"error":null},{"agent_name":"employment","skill":"employment_status","status":"completed","artifact":{"source":"workforce_solutions","subject_id":"S003","currently_employed":false,"employer_name":null,"employment_start_date":null,"tenure_years":null,"employment_type":null,"verified_annual_income":null,"income_currency":"USD","verification_confidence":"low","data_as_of":"2026-04-12"},"error":null},{"agent_name":"intl","skill":"international_credit_score","status":"skipped","artifact":null,"error":null}]}},"timeout_ms":30000,"attempt":1}' \
    | python3 -m json.tool
# Expect: decision "decline", risk_score > 60, risk_flags non-empty
```

---

### Step 4.10 — Commit Phase 4

```bash
$ git add .
$ git commit -m "Phase 4: LLM artifact generation, prompts, validation retry"
```

---

### ✅ Phase 4 Verification Checklist

- [ ] `docker compose up --build` completes without errors after Phase 4 changes
- [ ] `docker compose exec equifax env | grep OPENROUTER` shows `OPENROUTER_API_KEY` and `OPENROUTER_AGENT_MODEL`
- [ ] `docker compose exec synthesis env | grep OPENROUTER` shows `OPENROUTER_SYNTHESIS_MODEL`
- [ ] Equifax `POST /tasks/send` returns `status: "completed"` with `credit_score` between 300 and 850
- [ ] Calling Equifax three times with different subject names produces different `credit_score` values (at least 2 of 3 differ)
- [ ] Employment `POST /tasks/send` returns `status: "completed"` with `currently_employed` (bool) and valid `verification_confidence`
- [ ] International with `has_foreign_addr: true` → `data_availability` is `"full"` or `"partial"`
- [ ] International with `has_foreign_addr: false` → `data_availability` is `"unavailable"`, `reason` is non-null
- [ ] Synthesis with a valid outcome bundle → `status: "completed"`, `decision` is one of `approve / review / decline`
- [ ] Synthesis with a weak profile → `decision` is `"decline"` or `"review"` (not `"approve"`)
- [ ] Synthesis with a `timed_out` outcome → `status: "completed"`, `confidence` is `"medium"` or `"low"`
- [ ] `POST /verify` + poll `GET /verify/{task_id}` still reaches `"completed"` with the real LLM pipeline active
- [ ] `pytest tests/ -v` — all Phase 3 and Phase 4 tests pass
- [ ] `git log --oneline | head -2` shows Phase 4 commit on top

---

*Document: VerifyIQ Implementation Guide | Phases 3–4 | Companion to Spec v1.6*
*Next: Phase 5 — Orchestration Patterns*
