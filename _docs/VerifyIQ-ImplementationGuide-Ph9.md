# VerifyIQ — Implementation Guide
## Phase 9 — A2A Callee Endpoints + Mortgage Platform (UC-5)
*Companion to Project Specification v1.8*

---

## How to Use This Guide

This guide describes **what to build, why, and how to verify it**. It gives enough context to generate correct implementations without prescribing every line. Focus is on intent, structure, constraints, and verification — not code.

**Conventions:**

- `$` lines are terminal commands you run directly
- Pseudo code blocks show intent and structure, not copy-paste implementations
- Checklist items at the end must all pass before proceeding

---

## Phase 9 Overview

**Goal:** VerifyIQ becomes a true A2A peer. An external Mortgage Platform — a separate service representing a different organization — calls VerifyIQ via the A2A protocol, watches the pipeline execute via SSE, and receives the final `VerificationDecision` artifact.

This completes the aggregator model: VerifyIQ sits between external clients (who submit verification requests) and data provider agents (who supply credit, employment, and international data). Phase 9 adds the **inbound** side — VerifyIQ as a callee, not just a caller.

Phase 9 has four parts:
1. **A2A callee endpoints on Orchestrator** — `POST /tasks/send`, `GET /tasks/{id}`, `GET /tasks/{id}/stream`
2. **Mortgage Platform service** — minimal FastAPI + httpx app at `:9000` that calls VerifyIQ
3. **Docker Compose updates** — add the Mortgage Platform as an optional service
4. **CLI update** — enable the `mortgage-platform` scenario

**Estimated time:** 3–4 hours

**Prerequisite:** Phase 8 complete. `pytest tests/ -v` passes all tests. Auth tokens working.

---

## Background: Two-Tier Discovery

Before building, understand how discovery works across the two boundaries:

```
External clients (Mortgage Platform)
    |  <- Known URL + Agent Card (/.well-known/agent.json)
    |     External clients know VerifyIQ's URL from onboarding.
    |     They fetch the Agent Card to discover skills.
    |
VerifyIQ platform (Orchestrator + Synthesis + Registry)
    |  <- Agent Registry (GET /agents?skill=)
    |     Internal discovery. External callers never see this.
    |
Data provider agents (Equifax, Employment, Intl)
```

The Mortgage Platform knows the Orchestrator's URL via env var (`VERIFYIQ_URL`). This simulates real-world onboarding — you know your vendor's API endpoint because you signed up, not because you queried their internal service mesh. The Agent Card tells the Mortgage Platform *what VerifyIQ can do*.

The internal Agent Registry stays private. External callers never touch it.

---

## Why Not ADK?

The original Phase 9 guide prescribed Google's Agent Development Kit (ADK) for the callee endpoints. ADK was removed in spec v1.8. Reasons:

1. **ADK is a full agent framework, not an A2A library.** It wants to own your runtime and pulls in Google-ecosystem dependencies (Gemini SDK, Google Auth) that provide zero value in a model-agnostic project.
2. **The callee endpoints are ~60 lines of plain FastAPI.** Three routes, an in-memory dict for task mapping, and reuse of the existing `run_verification()` pipeline. There's nothing left for ADK to abstract.
3. **Hand-rolling teaches the protocol.** The whole point of this project is understanding A2A internals. ADK would hide them.

---

## Part A — A2A Callee Endpoints on Orchestrator

### What Changes

The Orchestrator already has two roles:
- **Caller** (UC-1–4): `POST /verify` from CLI → fan out to data agents → chain to synthesis. Fully built. Unchanged.
- **Callee** (UC-5, new): `POST /tasks/send` from external orchestrator → same internal pipeline → return artifact to external caller.

Both entry points call the same `run_verification()` function. The only new code is the bridge: accept an A2A task, map its ID to an internal one, start the pipeline, and expose status/streaming to the external caller.

**Two entry points, same pipeline:**

```
CLI → POST /verify                → run_verification(...)  <- existing, unchanged
Mortgage Platform → POST /tasks/send  → run_verification(...)  <- new
```

---

### Step 9.1 — External Task ID Mapping

**File:** `agents/orchestrator/__init__.py` — add module-level dict

External callers use their own task IDs. Internally, VerifyIQ generates its own `task_id` and `correlation_id`. A mapping dict bridges the two:

```python
_external_task_map: dict[str, dict[str, str]] = {}
# key: external task_id
# value: {"internal_task_id": "...", "correlation_id": "..."}
```

The external caller never sees internal IDs. It uses its own `task_id` for all operations.

---

### Step 9.2 — POST /tasks/send (Accept Inbound A2A Task)

**File:** `agents/orchestrator/__init__.py` — new route

This endpoint:
1. Validates the inbound `A2ATask` — only `skill="verify_subject"` is supported
2. Extracts the verification request from the task's `input` payload
3. Generates internal `task_id` and `correlation_id`
4. Stores the mapping in `_external_task_map`
5. Starts `run_verification()` as a background task (fire-and-forget)
6. Returns immediately with `{"task_id": external_id, "status": "submitted"}`

**Pseudo code:**

```python
@app.post("/tasks/send")
async def a2a_tasks_send(task: A2ATask, _token: str = Depends(require_auth)):
    if task.skill != "verify_subject":
        raise HTTPException(422, f"Unsupported skill: {task.skill}")

    body = VerificationRequest(**task.input)
    internal_task_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())

    _external_task_map[task.task_id] = {
        "internal_task_id": internal_task_id,
        "correlation_id": correlation_id,
    }

    task_manager.create_verification_request(internal_task_id, correlation_id, body)
    asyncio.create_task(run_verification(internal_task_id, correlation_id, body))

    return {"task_id": task.task_id, "status": "submitted"}
```

**Key:** Returns `"submitted"` immediately. The pipeline runs in the background. The external caller polls or streams to watch progress.

---

### Step 9.3 — GET /tasks/{task_id} (Poll Status)

**File:** `agents/orchestrator/__init__.py` — new route

The external caller polls this endpoint to check if the pipeline is done.

**Pseudo code:**

```python
@app.get("/tasks/{task_id}")
async def get_a2a_task(task_id: str, _token: str = Depends(require_auth)):
    mapping = _external_task_map.get(task_id)
    if not mapping:
        raise HTTPException(404, "Task not found")

    internal_id = mapping["internal_task_id"]
    result = task_manager.get_verification_request(internal_id)
    if not result:
        raise HTTPException(404, "Task not found")

    response = {
        "task_id": task_id,
        "status": result["status"],
        "correlation_id": mapping["correlation_id"],
        "internal_task_id": internal_id,
    }

    # If completed, extract the synthesis artifact
    if result["status"] == "completed":
        full = task_manager.get_full_verification(internal_id)
        synthesis = next(
            (t for t in full["agent_tasks"]
             if t["skill"] == "risk_synthesis" and t["status"] == "completed"),
            None,
        )
        response["artifact"] = synthesis["artifact"] if synthesis else None

    return response
```

**Status values:** `submitted`, `working`, `completed`, `failed`. The caller should poll until it sees a terminal status (`completed` or `failed`).

---

### Step 9.4 — GET /tasks/{task_id}/stream (SSE for External Caller)

**File:** `agents/orchestrator/__init__.py` — new route

Reuse the existing `SSEStreamer` to replay events for the external caller. Same mechanism as the CLI's SSE stream, just keyed by the external task ID.

**Pseudo code:**

```python
@app.get("/tasks/{task_id}/stream")
async def stream_a2a_task(task_id: str, _token: str = Depends(require_auth)):
    mapping = _external_task_map.get(task_id)
    if not mapping:
        raise HTTPException(404, "Task not found")

    internal_id = mapping["internal_task_id"]
    correlation_id = mapping["correlation_id"]

    async def event_stream():
        async for event in sse_streamer.stream(internal_id, correlation_id):
            yield event

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
```

The SSE streamer replays all past events from SQLite, then switches to live events. A client connecting after the pipeline finishes gets a pure database replay.

---

### Step 9.5 — Update Agent Card

**File:** `agents/orchestrator/agent_card.json` — update

The Orchestrator's Agent Card must advertise `verify_subject` so external callers know what it can do.

```json
{
  "name": "VerifyIQ Orchestrator",
  "url": "http://orchestrator:8000",
  "version": "1.0.0",
  "skills": [
    {
      "id": "verify_subject",
      "name": "Subject Verification",
      "description": "Full credit, employment, and international verification with risk synthesis"
    }
  ],
  "auth_schemes": ["bearer"],
  "capabilities": {
    "streaming": true,
    "push_notifications": false
  }
}
```

Served at `GET /.well-known/agent.json`. The Mortgage Platform fetches this to confirm VerifyIQ supports the skill it needs.

---

### Step 9.6 — Auth for Inbound Tasks

All three callee endpoints require bearer token auth (same `require_auth` dependency used elsewhere). A request without a valid `Authorization: Bearer <token>` header gets a 401.

No new auth code needed — reuse the existing `require_auth` FastAPI dependency.

---

## Part B — Mortgage Platform Service

### Design Principles

The Mortgage Platform is an **external service** representing a different organization. It:

- Lives at `mortgage-platform/` (outside `agents/` — not part of VerifyIQ's codebase)
- Has **zero imports** from `agents/shared/` or any VerifyIQ module
- Only knows the A2A wire protocol: Agent Card, `POST /tasks/send`, `GET /tasks/{id}`, SSE
- Uses plain httpx for HTTP calls (no ADK, no VerifyIQ client library)
- Knows the Orchestrator's URL from an env var (`VERIFYIQ_URL`) — simulates real-world onboarding

---

### Step 9.7 — Service Structure

```
mortgage-platform/
    main.py              # FastAPI app — discovery, dispatch, poll
    a2a_client.py        # Reusable A2A protocol client (httpx-based)
    agent_card.json      # Mortgage Platform's own Agent Card
    requirements.txt
    Dockerfile
```

**Requirements:**

```
fastapi
uvicorn[standard]
pydantic
httpx
structlog
```

No `google-adk`. No `agents.shared`. Protocol-only.

---

### Step 9.8 — A2A Client

**File:** `mortgage-platform/a2a_client.py`

A small, reusable httpx-based A2A client. It knows nothing about VerifyIQ specifically — just the A2A protocol:

```python
class A2AClient:
    def __init__(self, base_url: str, auth_token: str | None = None): ...

    def _headers(self) -> dict: ...
        # Content-Type: application/json
        # Authorization: Bearer <token> (if configured)

    async def discover(self) -> dict: ...
        # GET /.well-known/agent.json

    async def send_task(self, task: dict) -> dict: ...
        # POST /tasks/send

    async def get_task(self, task_id: str) -> dict: ...
        # GET /tasks/{task_id}

    async def stream_task(self, task_id: str) -> AsyncGenerator[dict]: ...
        # GET /tasks/{task_id}/stream — yields parsed SSE events

    async def send_and_wait(self, task: dict, poll_interval=2.0, timeout=90.0) -> dict: ...
        # send_task() then poll get_task() until terminal status or timeout
```

**Key:** `send_and_wait()` is the convenience method. It submits the task then polls every N seconds until the status is `completed`, `failed`, or the timeout expires.

---

### Step 9.9 — Main Application

**File:** `mortgage-platform/main.py`

~100 lines. Exposes a `/trigger` endpoint that runs the full A2A workflow:

1. Create an `A2AClient` pointing at `VERIFYIQ_URL`
2. **Discover**: Fetch VerifyIQ's Agent Card, verify it advertises `verify_subject`
3. **Build task**: Construct an `A2ATask` with `skill="verify_subject"` and the subject payload
4. **Send and wait**: Submit the task, poll until completion
5. **Log result**: Print the decision

**Pseudo code:**

```python
VERIFYIQ_URL = os.environ.get("VERIFYIQ_URL", "http://orchestrator:8000")
AUTH_TOKEN = os.environ.get("VERIFYIQ_AUTH_TOKEN")

@app.post("/trigger")
async def trigger_verification(req: TriggerRequest = default_subject):
    client = A2AClient(VERIFYIQ_URL, AUTH_TOKEN)

    # 1. Discover
    card = await client.discover()
    skills = [s["id"] if isinstance(s, dict) else s for s in card.get("skills", [])]
    if "verify_subject" not in skills:
        raise HTTPException(400, "VerifyIQ does not advertise verify_subject")

    # 2. Build task
    task = {
        "task_id": str(uuid.uuid4()),
        "skill": "verify_subject",
        "input": req.model_dump(),
    }

    # 3. Send and wait
    result = await client.send_and_wait(task, poll_interval=2.0, timeout=90.0)
    return result
```

**Default subject:** David Kim (`SIM-KR-2018`), mortgage use case, `has_foreign_addr=True`. This exercises the full four-agent pipeline (Equifax + Employment + International + Synthesis).

The Mortgage Platform also serves its own Agent Card at `GET /.well-known/agent.json` — it advertises `loan_origination`, not verification skills. It *consumes* VerifyIQ as a sub-agent.

---

### Step 9.10 — Dockerfile

**File:** `mortgage-platform/Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY mortgage-platform/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mortgage-platform/ .

EXPOSE 9000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]
```

**Note:** Unlike data agents, the Mortgage Platform does NOT copy `agents/shared/` into its container. It has zero access to VerifyIQ internals. This is the protocol boundary in action.

---

## Part C — Docker Compose Updates

### Step 9.11 — Add Mortgage Platform Service

**File:** `docker-compose.yml` — add:

```yaml
  mortgage-platform:
    build:
      context: .
      dockerfile: mortgage-platform/Dockerfile
    ports:
      - "9000:9000"
    environment:
      - VERIFYIQ_URL=http://orchestrator:8000
      - VERIFYIQ_AUTH_TOKEN=${VERIFYIQ_AUTH_TOKEN}
    depends_on:
      orchestrator:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/health"]
      interval: 10s
      timeout: 5s
      retries: 3
    profiles:
      - uc5
```

**The `profiles` key is critical.**

`profiles: [uc5]` means the Mortgage Platform does NOT start with `docker compose up`. It only starts when explicitly requested:

```bash
# Normal UC-1-4 workflow — Mortgage Platform does not start
$ docker compose up -d

# UC-5 demo — include the Mortgage Platform
$ docker compose --profile uc5 up -d
```

Why a separate profile? The Mortgage Platform represents an external organization. Starting it alongside the core stack blurs that boundary. The two-command split reinforces the architecture: "here's your platform" vs. "here's someone else calling it."

---

## Part D — CLI Update

### Step 9.12 — Enable the `mortgage-platform` Scenario

**File:** `cli/verifyiq_cli/commands/run.py`

The `mortgage-platform` scenario triggers the Mortgage Platform's `/trigger` endpoint:

```python
if scenario == "mortgage-platform":
    mp_url = os.environ.get("MORTGAGE_PLATFORM_URL", "http://localhost:9000")
    response = httpx.post(f"{mp_url}/trigger", headers=auth_headers, timeout=120.0)
    # render result
    return
```

Include a clear error message if the Mortgage Platform isn't running (connection refused → "Start the Mortgage Platform with `docker compose --profile uc5 up`").

---

## Part E — UC-5 Tests

### Step 9.13 — Test File

**File:** `tests/test_phase9_uc5.py`

These are integration tests requiring all services + the Mortgage Platform to be running.

```bash
$ docker compose --profile uc5 up --build -d
$ sleep 15
$ export VERIFYIQ_AUTH_TOKEN=verifyiq-dev-token-2026
$ pytest tests/test_phase9_uc5.py -v
```

---

### Tests (7 tests)

**1. `test_orchestrator_agent_card_has_bearer_auth`**

`GET /.well-known/agent.json` on the Orchestrator.

Assertions:
- Skills list includes `verify_subject`
- `capabilities.streaming` is `true`
- `auth_schemes` includes `"bearer"`

---

**2. `test_tasks_send_returns_submitted`**

`POST /tasks/send` with a valid task and auth header.

Assertions:
- Returns within 3 seconds (does not block on pipeline)
- Response contains `task_id` matching what was sent
- Response contains `status: "submitted"`

---

**3. `test_tasks_send_rejects_missing_auth`**

`POST /tasks/send` without auth header.

Assertions:
- Returns 401

---

**4. `test_tasks_send_rejects_wrong_skill`**

`POST /tasks/send` with `skill: "nonexistent_skill"`.

Assertions:
- Returns 422

---

**5. `test_tasks_get_returns_completed`**

Full A2A round-trip: `POST /tasks/send`, then poll `GET /tasks/{task_id}` until terminal.

Assertions:
- Final status is `"completed"`
- `artifact` is present
- Artifact contains `decision` (one of: approve, review, decline)

---

**6. `test_tasks_stream_delivers_events`**

`POST /tasks/send`, then connect to `GET /tasks/{task_id}/stream` after completion (replay mode).

Assertions:
- At least 3 SSE events received
- Events include `agents_resolved`
- Terminal event is `completed` or `failed`

---

**7. `test_mortgage_platform_trigger_completes`**

`POST /trigger` on the Mortgage Platform (port 9000).

Assertions:
- Completes within 120 seconds
- Response contains `status: "completed"`
- Response contains an artifact with a `decision`

This is the full end-to-end: Mortgage Platform discovers VerifyIQ via Agent Card, submits A2A task, polls until done, returns the decision.

---

## Common Failure Modes

| Symptom | Likely Cause |
|---|---|
| `POST /tasks/send` returns 404 | Callee routes not added to orchestrator |
| `POST /tasks/send` returns 422 | Payload shape mismatch or wrong skill name |
| Mortgage Platform can't discover Agent Card | `VERIFYIQ_URL` env var wrong; or agent card missing `verify_subject` skill |
| Pipeline runs but external caller gets no SSE events | SSE stream not bridged via `_external_task_map` |
| `correlation_id` not propagating | Callee handler not generating `correlation_id` or not passing it to `run_verification` |
| Mortgage Platform times out | Orchestrator not healthy yet; increase poll timeout |
| Tests pass individually but fail together | `_external_task_map` (in-memory) not isolated between tests |

---

## Commit

```bash
$ git add .
$ git commit -m "Phase 9: A2A callee endpoints + Mortgage Platform UC-5"
```

---

## Phase 9 Verification Checklist

**A2A Callee (Orchestrator):**
- [ ] `POST /tasks/send` accepts A2A task and returns `task_id` + `"submitted"` immediately
- [ ] `GET /tasks/{task_id}` returns `"working"` while pipeline runs
- [ ] `GET /tasks/{task_id}` returns `"completed"` with `VerificationDecision` artifact after pipeline finishes
- [ ] `GET /tasks/{task_id}/stream` delivers SSE events to external caller
- [ ] `POST /tasks/send` without auth token returns 401
- [ ] `POST /tasks/send` with unsupported skill returns 422
- [ ] Internal pipeline runs identically to UC-1–4 (same agents, same fan-out, same synthesis)
- [ ] `correlation_id` propagates from inbound task through all internal `agent_tasks` rows

**Agent Card:**
- [ ] `GET /.well-known/agent.json` on Orchestrator includes `verify_subject` skill
- [ ] `capabilities.streaming` is `true`
- [ ] `auth_schemes` includes `"bearer"`

**Mortgage Platform:**
- [ ] Service starts at `:9000` and responds to `GET /health`
- [ ] `GET /.well-known/agent.json` returns Mortgage Platform's own Agent Card
- [ ] `POST /trigger` discovers VerifyIQ Agent Card, dispatches A2A task, polls until done
- [ ] Does NOT import from `agents/shared/` — protocol-only integration
- [ ] Uses plain httpx — no ADK, no VerifyIQ client library

**Docker Compose:**
- [ ] `docker compose up -d` does NOT start the Mortgage Platform
- [ ] `docker compose --profile uc5 up -d` starts all services including Mortgage Platform
- [ ] Mortgage Platform depends on Orchestrator being healthy
- [ ] Port 9000 exposed and accessible

**CLI:**
- [ ] `verifyiq run mortgage-platform` triggers the platform or shows instructions
- [ ] All other scenarios (UC-1 through UC-4) still work unchanged

**Tests:**
- [ ] `pytest tests/ -v` — all Phase 3, 5, 6, 7, 8, and 9 tests pass
- [ ] `pytest tests/test_phase9_uc5.py -v` — all 7 UC-5 tests pass

**Demo Flow:**
- [ ] Start full stack: `docker compose --profile uc5 up --build -d`
- [ ] Watch Mortgage Platform logs: `docker compose logs -f mortgage-platform`
- [ ] See: discovery -> task dispatch -> polling -> final decision
- [ ] Mortgage Platform has NO knowledge of Equifax, LangGraph, or OpenRouter
- [ ] `docker compose logs | grep <correlation_id>` traces the full request across all containers

---

*Document: VerifyIQ Implementation Guide | Phase 9 | Companion to Spec v1.8*
