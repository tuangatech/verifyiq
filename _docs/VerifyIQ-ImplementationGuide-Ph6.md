# VerifyIQ — Implementation Guide
## Phase 6 — LangGraph Employment Agent + SSE Streaming
*Companion to Project Specification v1.7*

---

## How to Use This Guide

This guide describes **what to build, why, and how to verify it**. It gives Claude Code enough context to generate correct implementations without prescribing every line. Focus is on intent, structure, constraints, and verification — not code.

**Conventions:**

- `$` lines are terminal commands you run directly
- ✅ Checklist items at the end of the phase must all pass before proceeding

---

## Phase 6 Overview

**Goal:** Two independent improvements combined in one phase because neither depends on the other and together they make the system meaningfully more complete:

1. **LangGraph Employment Agent** — Replace the flat single-LLM-call implementation with a real state graph that has conditional branching. Demonstrates framework-agnostic A2A interop: the Orchestrator sees zero difference.

2. **SSE Streaming** — Add `GET /verify/{task_id}/stream` so clients can watch pipeline progress in real time instead of polling blindly.

**Estimated time:** 4–5 hours

**Prerequisite:** Phase 5 complete. `pytest tests/ -v` passes all Phase 3 and Phase 5 tests.

---

## Part A — LangGraph Employment Agent

### Why LangGraph Here (and Only Here)

Employment verification has genuine branching: check current employer → if not found, fall back to prior employer → verify income → compute tenure. This is a stateful multi-step pipeline with conditional routing — exactly what LangGraph handles well.

The key learning objective: **the Orchestrator never knows LangGraph exists.** Same `POST /tasks/send`, same `A2ATaskResult` schema, same Agent Card skills. LangGraph is hidden behind the A2A protocol boundary. If the test suite passes without modification, the interop is clean.

---

### Step 6.1 — Graph State

**File:** `agents/employment/graph_state.py` — new file.

A `TypedDict` (with `total=False`) that flows through all graph nodes. Nodes read what they need and write their outputs progressively.

**Fields needed:**
- **Input** (set once at entry): `subject_id`, `subject_name`, `use_case`, `today`
- **Employer lookup output**: `currently_employed`, `employer_name`, `employment_start_date`, `employment_type`
- **Prior employer fallback**: `prior_employer_name`, `prior_employer_end_date`
- **Income**: `verified_annual_income`, `income_currency`
- **Computed**: `tenure_years`, `verification_confidence`
- **Error**: `error` (if any node fails, short-circuits to end)

Use `total=False` so the graph can build state incrementally — not all fields exist after every node.

---

### Step 6.2 — Graph Nodes (5 functions)

**File:** `agents/employment/nodes.py` — new file.

Each node is a function that takes the state TypedDict and returns a partial dict update. Three nodes call OpenRouter; two are pure computation.

| Node | LLM? | Purpose | Conditional? |
|---|---|---|---|
| `lookup_current_employer` | Yes | Determines if currently employed; sets employer fields | Always runs (entry) |
| `lookup_prior_employer` | Yes | Finds most recent prior employer | Only if `currently_employed == False` |
| `verify_income` | Yes | Generates income from payroll context | Always (after employer) |
| `calculate_tenure` | No | Computes `tenure_years` from dates; sets `verification_confidence` | Always |
| `build_artifact` | No | Validates completeness; handles error state | Always (final before END) |

**Design rationale:** Splitting the single LLM call into 3 focused calls produces better structured output — each prompt asks for fewer fields with tighter schema constraints. `calculate_tenure` is pure math (no LLM needed). `build_artifact` is a validation gate.

**LLM calling pattern in nodes:** Same as other agents — `httpx.AsyncClient` → OpenRouter → `response_format: {"type": "json_object"}` → parse JSON. Each LLM-calling node has its own focused prompt asking for only its fields. On exception, set `state["error"]` and the graph routes to terminal.

---

### Step 6.3 — Per-Node Prompts

**File:** `agents/employment/prompts.py` — rewrite existing.

Replace the monolithic system+user prompt with prompt builder functions per node:

- `current_employer_prompt(subject_id, subject_name, use_case)` → `(system, user)` tuple
- `prior_employer_prompt(subject_id, subject_name, use_case)` → `(system, user)` tuple
- `income_prompt(subject_name, employer_name, employment_type, use_case)` → `(system, user)` tuple

Each system prompt establishes the "Workforce Solutions database" persona and specifies the exact JSON schema for that node's output only. Keep the same realistic-variation guidelines from the current prompt (70-80% employed, income ranges by type, use-case weighting).

A shared `BASE_SYSTEM_PROMPT` constant avoids repeating the persona setup across all three.

---

### Step 6.4 — Build the Graph

**File:** `agents/employment/graph.py` — new file.

Constructs and compiles a `StateGraph` from `langgraph.graph`. Export a `build_employment_graph()` function that returns the compiled graph.

**Graph topology:**

```
lookup_current_employer
        │
        ├── currently_employed == True ──→ verify_income ─→ calculate_tenure ─→ build_artifact ─→ END
        │
        └── currently_employed == False ─→ lookup_prior_employer ─→ verify_income ─→ ...same
```

One conditional edge after `lookup_current_employer`. If `state["error"]` is set at any point, short-circuit to `build_artifact`.

Compile once at module level — compiled graphs are stateless and safe to reuse across requests. Invoke with `await compiled.ainvoke(initial_state)`.

---

### Step 6.5 — Rewrite `main.py`

**File:** `agents/employment/main.py` — rewrite.

The `POST /tasks/send` handler changes from "call LLM directly" to "build initial state → `await graph.ainvoke(state)` → construct `EmploymentArtifact` from terminal state." Handle three error paths: graph exception (`GRAPH_EXECUTION_FAILED`), node-level error in state (`LLM_VALIDATION_FAILED`), artifact Pydantic validation failure (`LLM_VALIDATION_FAILED`).

Everything else stays identical: `lifespan`, Agent Card, `/health`, `GET /tasks/{task_id}`, response schema. Delete old `tools.py`.

---

### Step 6.6 — Dependencies and Dockerfile

`langgraph` is already in `requirements.txt`. Add `langchain-core>=0.3.0` if needed. Dockerfile needs no changes — new files are in `agents/employment/` and already copied. Verify `agents/employment/__init__.py` exists for package imports.

---

### Step 6.7 — Smoke Test

```bash
$ docker compose down && docker compose up --build -d
```

Verify registration:
```bash
$ docker compose logs employment 2>&1 | grep -i "register"
```

Direct call:
```bash
$ curl -s -X POST http://localhost:8002/tasks/send \
    -H "Content-Type: application/json" \
    -d '{"task_id":"lg-test","correlation_id":"corr-lg","skill":"employment_status","input":{"subject_id":"S1","subject_name":"Jane Doe","use_case":"mortgage"},"timeout_ms":30000,"attempt":1}' \
    | python -m json.tool
```

Expect: `status: "completed"`, artifact with `tenure_years` (computed), `verified_annual_income` (separate LLM call), `verification_confidence` (computed from tenure).

---

### Step 6.8 — Regression Check

```bash
$ pytest tests/test_phase3_a2a.py::test_employment_task_send_returns_completed -v
$ pytest tests/test_phase5_orchestration.py -v
```

All must pass unchanged. If they don't, the graph is producing an artifact that violates the `EmploymentArtifact` schema — fix the nodes, not the tests.

---

## Part B — SSE Streaming

### Why SSE

The verification pipeline takes 5–15 seconds (multiple LLM calls). Without SSE, clients poll blindly and the user sees nothing until completion. With SSE, each milestone is visible as it happens.

The same endpoint serves both future consumers:
- **CLI** (Phase 7): renders events as a rich live timeline
- **Mortgage Platform** (Phase 9): logs structured progress events

Architecture ensures client disconnect doesn't affect the pipeline — the pipeline is a fire-and-forget `asyncio.create_task`, SSE is an optional observer.

---

### Step 6.9 — SSE Event Types

Define 9 event types emitted during a verification pipeline:

| Event type | When | Key payload fields |
|---|---|---|
| `agents_resolved` | After Registry skill resolution | `agents` (list of names) |
| `agent_started` | Parallel agent task dispatched | `agent`, `skill` |
| `agent_completed` | Parallel agent returns success | `agent`, `status` |
| `agent_failed` | Parallel agent fails/times out | `agent`, `status`, `error` |
| `agent_skipped` | Agent not needed for this use case | `agent`, `reason` |
| `synthesis_started` | Risk Synthesis dispatch begins | `agent` |
| `synthesis_completed` | Risk Synthesis returns | `decision`, `confidence` |
| `completed` | Pipeline finished successfully | `task_id`, `decision` |
| `failed` | Pipeline failed | `task_id`, `error` |

Every event payload includes `correlation_id`. Wire format follows the SSE spec: `event:` line, `data:` line (JSON), `id:` line (sequence number), blank line terminator.

---

### Step 6.10 — SSEStreamer Class

**File:** `agents/orchestrator/sse.py` — new file.

An `asyncio.Queue`-per-request design that also persists to SQLite for replay:

- `emit()` writes to both the live queue and `sse_events` table
- `stream()` is an async generator: replays historical events from DB (catch-up), then yields live events from the queue, stopping on a `None` sentinel
- `create_stream()` / `complete()` manage the lifecycle

**Why both queue and DB:** Queue gives zero-latency to connected clients. DB gives replay for late connectors. Both are cheap (~15 events per pipeline run).

**Why sentinel:** The `stream()` generator blocks on `queue.get()`. A `None` value signals "pipeline done" so the generator returns and the HTTP connection closes cleanly.

---

### Step 6.11 — Add DB Methods

**File:** `agents/orchestrator/db.py` — add two methods to `TaskManager`.

- `write_sse_event(correlation_id, event_type, payload)` — INSERT into `sse_events`
- `get_sse_events(correlation_id)` — SELECT all events ordered by `id ASC`

The `sse_events` table already exists (created in Phase 3 `init_db`). These methods simply use it.

---

### Step 6.12 — SSE Endpoint

**File:** `agents/orchestrator/__init__.py` — add endpoint.

`GET /verify/{task_id}/stream` returns a FastAPI `StreamingResponse` with `media_type="text/event-stream"`. Headers: `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`.

The response body is the `sse_streamer.stream()` async generator. Works whether called before the pipeline starts (live) or after (replay from DB).

---

### Step 6.13 — Wire Emission into `run_verification`

**File:** `agents/orchestrator/__init__.py` — modify existing function.

Add `emit()` calls at each milestone within `run_verification`:

1. After `resolver.find_all()` → emit `agents_resolved`
2. Before each parallel dispatch → emit `agent_started`
3. After each parallel dispatch returns → emit `agent_completed` or `agent_failed`
4. When injecting skipped outcomes → emit `agent_skipped`
5. Before synthesis dispatch → emit `synthesis_started`
6. After synthesis returns → emit `synthesis_completed`
7. At the end → emit `completed` or `failed`

**Critical:** call `sse_streamer.create_stream(task_id)` at the top of `run_verification` and `await sse_streamer.complete(task_id)` in a `finally` block. The `finally` guarantees the stream closes even on exception — without it, connected clients hang forever.

---

### Step 6.14 — Client Disconnect Safety

No special code needed. `run_verification` is a fire-and-forget `asyncio.create_task` — completely decoupled from the SSE connection. If a client disconnects, the pipeline continues and events persist to SQLite. Reconnecting clients get catch-up replay.

---

### Step 6.15 — Smoke Test SSE

```bash
# Submit a request, capture task_id
$ TASK_ID=$(curl -s -X POST http://localhost:8000/verify \
    -H "Content-Type: application/json" \
    -d '{"subject_name":"SSE Test","subject_id":"S-sse","use_case":"mortgage","has_foreign_addr":true,"consent":true}' \
    | python -c "import sys,json; print(json.load(sys.stdin)['task_id'])")

# Watch events arrive live (curl -N disables output buffering)
$ curl -N http://localhost:8000/verify/$TASK_ID/stream
```

Expect: events arriving progressively over 5–15 seconds, ending with `event: completed`. Connection closes after final event. Late-connecting after completion should replay all events instantly from DB.

---

## Phase 6 Tests

**File:** `tests/test_phase6_langgraph_sse.py` — 12 tests total (6 LangGraph + 6 SSE).

Use the same helper patterns as Phase 5 tests: `_verify_request()`, `_submit_and_wait()`, `orchestrator_client` / `employment_client` fixtures.

---

### LangGraph Tests (6)

| # | Test | What it verifies |
|---|---|---|
| 1 | `test_employment_langgraph_returns_valid_artifact` | Direct call to employment agent → `completed`, artifact matches `EmploymentArtifact` schema (source, subject_id, currently_employed, verification_confidence, data_as_of) |
| 2 | `test_employment_langgraph_branching_fields` | Whichever branch runs (employed/unemployed), all downstream fields are populated: `tenure_years`, `verified_annual_income`, `employer_name` |
| 3 | `test_employment_langgraph_income_populated` | `verified_annual_income` > 0, `income_currency` == "USD" |
| 4 | `test_employment_langgraph_tenure_calculated` | If `employment_start_date` present → `tenure_years` > 0. `verification_confidence` is one of high/medium/low |
| 5 | `test_employment_langgraph_correlation_id_preserved` | Sent `correlation_id` echoed back unchanged |
| 6 | `test_employment_langgraph_multiple_calls_vary` | 3 calls with same subject → at least 2 have different `employer_name` or `verified_annual_income` (proves LLM variation, not canned responses) |

**What these tests prove together:** The graph executes correctly regardless of which branch the LLM triggers, all five nodes contribute their expected fields, and the A2A contract is maintained.

---

### SSE Tests (6)

| # | Test | What it verifies |
|---|---|---|
| 7 | `test_sse_stream_emits_events` | Connect to stream, collect events until `completed` → at least 4 events, includes `agents_resolved` and `completed` |
| 8 | `test_sse_events_contain_correlation_id` | Every event payload has `correlation_id` matching the one from `POST /verify` |
| 9 | `test_sse_progress_before_completion` | At least one `agent_started` or `agent_completed` arrives before `completed`. `agents_resolved` precedes `agent_completed`. `synthesis_started` follows all parallel completions. |
| 10 | `test_sse_events_persisted_to_db` | After pipeline completes, late-connect to stream → replays events from DB. At least 4 events, correctly ordered |
| 11 | `test_sse_client_disconnect_does_not_crash_pipeline` | Connect to stream, read 1-2 events, disconnect. Poll `GET /verify/{task_id}` → reaches `completed`. All agent_tasks present |
| 12 | `test_sse_skipped_agent_event_emitted` | Submit `rental` request → `agent_skipped` event for `intl`, no `agent_started` for `intl`, pipeline still completes |

**SSE test notes:** Use `httpx` streaming (`client.stream("GET", url)` + `aiter_lines()`). Parse `data:` lines as JSON. Always break on `completed`/`failed` events — otherwise tests hang. For disconnect test: close the stream context early, then poll status separately.

---

## Running Tests

```bash
$ docker compose up --build -d
$ pytest tests/test_phase6_langgraph_sse.py -v    # Phase 6 only
$ pytest tests/ -v                                 # Full regression
```

**Common failure modes:**

| Symptom | Likely Cause |
|---|---|
| Employment agent won't start | Missing `__init__.py` or `langchain-core` dependency |
| Graph hangs (timeout) | Async node not awaited — verify using `ainvoke` not `invoke` |
| Artifact validation fails after graph | Node returning wrong types — `employment_start_date` must be ISO string |
| SSE stream never closes | `sse_streamer.complete()` not in `finally` block |
| Late replay returns empty | `get_sse_events` query bug or `_completed` flag not set |
| Disconnect test fails | Pipeline coroutine tied to request lifecycle instead of `asyncio.create_task` |

---

## Commit

```bash
$ git add .
$ git commit -m "Phase 6: LangGraph Employment Agent + SSE streaming"
```

---

## ✅ Phase 6 Verification Checklist

**LangGraph Employment Agent:**
- [ ] Employment container starts and registers with Registry
- [ ] Direct `POST /tasks/send` to `:8002` returns `completed` with valid artifact
- [ ] Artifact has `tenure_years` (graph-computed, not LLM-generated)
- [ ] Artifact has `verified_annual_income` (separate node's LLM call)
- [ ] Artifact has `verification_confidence` (computed from tenure)
- [ ] Multiple calls produce varied results
- [ ] Both branches work: employed path (3 LLM calls) and unemployed path (4 LLM calls including prior employer)

**Framework Interop (most important):**
- [ ] Phase 3 `test_employment_task_send_returns_completed` passes unchanged
- [ ] Phase 5 full orchestration tests pass unchanged
- [ ] No LangGraph imports anywhere in Orchestrator code
- [ ] Employment Agent Card unchanged — same skills advertised

**SSE Streaming:**
- [ ] `GET /verify/{task_id}/stream` returns `Content-Type: text/event-stream`
- [ ] Live stream shows events arriving progressively over 5–15 seconds
- [ ] `agents_resolved` emitted before `agent_completed` events
- [ ] Skipped agents produce `agent_skipped` events (test with `rental`)
- [ ] `completed` is the final event — stream closes after it
- [ ] Every event has `correlation_id`
- [ ] Late-connecting client receives full replay from DB
- [ ] Client disconnect does not affect pipeline completion
- [ ] `sse_events` table has rows for all emitted events

**Full Regression:**
- [ ] `pytest tests/ -v` — all Phase 3, 5, and 6 tests pass
- [ ] `git log --oneline | head -2` shows Phase 6 commit

---

*Document: VerifyIQ Implementation Guide | Phase 6 | Companion to Spec v1.7*
*Next: Phase 7 — CLI (typer + rich)*
