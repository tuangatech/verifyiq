# VerifyIQ — Implementation Guide
## Phase 9 — ADK Server-Side + Mortgage Platform (UC-5)
*Companion to Project Specification v1.7*

---

## How to Use This Guide

This guide describes **what to build, why, and how to verify it**. It gives enough context to generate correct implementations without prescribing every line. Focus is on intent, structure, constraints, and verification — not code.

**Conventions:**

- `$` lines are terminal commands you run directly
- Pseudo code blocks show intent and structure, not copy-paste implementations
- ✅ Checklist items at the end must all pass before proceeding

---

## Phase 9 Overview

**Goal:** VerifyIQ becomes a true A2A peer. An external Mortgage Platform Orchestrator — a separate service you don't control — calls VerifyIQ via the A2A protocol, watches the pipeline execute via SSE, and receives the final `VerificationDecision` artifact. ADK manages the server-side protocol on the Orchestrator so you don't hand-roll reconnects, streaming backpressure, or task cancellation.

Phase 9 has five parts:
1. **ADK server-side on Orchestrator** — `POST /tasks/send` inbound handler via ADK's `AgentExecutor`
2. **Mortgage Platform service** — minimal ADK agent at `:9000` that calls VerifyIQ
3. **Docker Compose updates** — add the Mortgage Platform as an optional service
4. **CLI update** — enable the `mortgage-platform` scenario
5. **UC-5 tests** — full round-trip from external caller through internal pipeline

**Estimated time:** 4–6 hours

**Prerequisite:** Phase 8 complete. `pytest tests/ -v` passes all tests. Auth tokens working.

---

## Background: What ADK Does Here

Before building, understand exactly what ADK handles and what it does not.

**The Orchestrator already has two roles:**
- **Caller** (UC-1–4): Receives `POST /verify` from CLI, fans out A2A tasks to data agents, chains to synthesis. This path is fully built — plain FastAPI + httpx. ADK does **not** touch this path.
- **Callee** (UC-5): Receives an inbound A2A task from an external orchestrator via `POST /tasks/send`. This path is new. ADK manages the server-side protocol here.

**What ADK manages on the callee path:**

| Concern | Without ADK | With ADK |
|---|---|---|
| Accept inbound task | Parse JSON, validate fields, return `task_id` | ADK validates against A2A spec automatically |
| Task state machine | Hand-roll `submitted → working → completed/failed` visible to caller | ADK manages state transitions + exposes `GET /tasks/{id}` |
| SSE to external caller | Hand-roll `StreamingResponse` with reconnect handling | ADK manages stream lifecycle, backpressure, reconnects |
| Task cancellation | Hand-roll cancellation signal propagation | ADK handles `POST /tasks/{id}/cancel` |
| Agent Card | Static JSON route (already exists) | ADK can generate from config (but static route is fine too) |

**What ADK does NOT do:**
- It does not change how the Orchestrator dispatches outbound tasks to data agents (still `TaskDispatcher` + httpx)
- It does not change the internal pipeline logic (`run_verification`)
- It does not replace the existing `POST /verify` endpoint for CLI usage
- Data agents remain plain FastAPI — ADK adds no value on their simple server side

**Two entry points, same pipeline:**

```
CLI → POST /verify           → run_verification(...)  ← existing, unchanged
Mortgage Platform → POST /tasks/send (ADK) → run_verification(...)  ← new
```

The only new code is the bridge: ADK receives the inbound task, extracts the verification request from the task payload, calls the same `run_verification()` function, and packages the result back as an A2A task result.

---

## Part A — ADK Server-Side on Orchestrator

### Step 9.1 — Install ADK

**File:** `agents/orchestrator/requirements.txt` — add:

```
google-adk>=0.5.0
```

> **Version note:** ADK is actively evolving. Pin to a minimum version that supports `AgentExecutor` and A2A task handling. Check [PyPI](https://pypi.org/project/google-adk/) for the latest stable release at implementation time. If the API has changed significantly, adapt the patterns below to the current ADK API — the architectural intent remains the same.

> **ADK dependency note:** ADK pulls in its own dependencies (e.g. `starlette`, `pydantic`). Verify there are no version conflicts with the existing FastAPI stack. If conflicts arise, pin the overlapping packages to compatible versions in `requirements.txt`.

---

### Step 9.2 — Understand ADK's AgentExecutor Pattern

ADK's server-side handler follows this pattern:

1. You define an **agent function** (or class) that receives a task context and produces results
2. You wrap it in an `AgentExecutor` that handles the A2A protocol
3. The executor mounts routes on your ASGI app: `POST /tasks/send`, `GET /tasks/{id}`, `GET /tasks/{id}/stream`, `POST /tasks/{id}/cancel`
4. When a task arrives, ADK calls your agent function, manages state transitions, and streams events back to the caller

**Key architectural decision:** ADK's routes and the existing FastAPI routes coexist on the same app. The existing `POST /verify` (CLI path) is a plain FastAPI route. The new `POST /tasks/send` (external caller path) is an ADK-managed route. Both call the same `run_verification()` pipeline internally.

---

### Step 9.3 — Create the ADK Agent Handler

**File:** `agents/orchestrator/a2a_handler.py` (new)

This module bridges ADK's inbound task handling to the existing pipeline.

**Responsibilities:**
1. Receive the A2A task from ADK
2. Extract the verification request from the task's `input` payload
3. Generate `task_id` and `correlation_id`
4. Start `run_verification()` as a background coroutine (same as `POST /verify` does)
5. Yield progress updates back to ADK so it can stream them to the caller
6. Return the final `VerificationDecision` artifact when the pipeline completes

**Pseudo code:**

```python
# agents/orchestrator/a2a_handler.py

import asyncio
import uuid

from agents.orchestrator.models import VerificationRequest
from agents.orchestrator.db import TaskManager
from agents.orchestrator.sse import SSEStreamer

# These will be injected or imported from the main module
task_manager: TaskManager = ...
sse_streamer: SSEStreamer = ...


async def handle_verification_task(task_context):
    """ADK agent function — called when an external orchestrator sends a task.

    task_context is provided by ADK and contains:
      - task_context.task: the inbound A2A task (task_id, input, etc.)
      - task_context.send_status_update(message): emit a progress event
      - task_context.complete(artifact): mark task as completed with artifact
      - task_context.fail(error): mark task as failed
    """
    inbound_task = task_context.task
    input_data = inbound_task.input  # dict with verification request fields

    # Validate the input matches VerificationRequest shape
    try:
        body = VerificationRequest(**input_data)
    except Exception as e:
        await task_context.fail(f"Invalid input: {e}")
        return

    # Generate internal IDs (the inbound task_id is the external caller's ID;
    # we generate our own internal task_id for the pipeline)
    internal_task_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())

    # Persist the verification request (same as POST /verify)
    task_manager.create_verification_request(internal_task_id, correlation_id, body)

    # Notify caller that work has started
    await task_context.send_status_update("Pipeline started")

    # Run the pipeline (same function used by POST /verify)
    # Import here to avoid circular dependency
    from agents.orchestrator import run_verification
    await run_verification(internal_task_id, correlation_id, body)

    # After pipeline completes, read the result
    result = task_manager.get_verification_request(internal_task_id)

    if result and result.get("status") == "completed":
        # Fetch the synthesis artifact from agent_tasks
        full = task_manager.get_full_verification(internal_task_id)
        synthesis_task = next(
            (t for t in full["agent_tasks"] if t["agent_name"] == "synthesis"),
            None,
        )
        artifact = synthesis_task["artifact"] if synthesis_task else {}

        await task_context.complete(artifact)
    else:
        await task_context.fail("Pipeline failed or did not complete")
```

**Important design notes:**

- The inbound `task_id` from the Mortgage Platform is the **external** task ID — ADK manages this. Internally we generate our own `task_id` and `correlation_id` for the pipeline.
- `run_verification()` is the same function called by `POST /verify`. No duplication of pipeline logic.
- ADK handles streaming the status updates back to the Mortgage Platform. We call `task_context.send_status_update()` at key milestones.
- If you want richer streaming, you can also subscribe to the internal SSE stream and forward events through `task_context.send_status_update()`.

---

### Step 9.4 — Integrate ADK into the FastAPI App

**File:** `agents/orchestrator/__init__.py` — modify

Mount ADK's routes alongside existing FastAPI routes.

**Pseudo code:**

```python
# agents/orchestrator/__init__.py — additions

from google.adk import AgentExecutor  # exact import path depends on ADK version
from .a2a_handler import handle_verification_task

# Option A: ADK mounts on the existing FastAPI app
# ADK provides a way to add A2A routes to an existing ASGI app.
# The exact API depends on the ADK version. Common patterns:

# Pattern 1: ADK as middleware/router
agent_executor = AgentExecutor(
    agent_card=AGENT_CARD,
    handler=handle_verification_task,
)
# Mount ADK routes (POST /tasks/send, GET /tasks/{id}, etc.)
agent_executor.mount(app)

# Pattern 2: ADK as a sub-application
# adk_app = agent_executor.as_asgi()
# app.mount("/a2a", adk_app)  # routes at /a2a/tasks/send

# Pattern 3: Manual route wiring
# @app.post("/tasks/send")
# async def tasks_send(request: Request):
#     return await agent_executor.handle_task(request)
```

> **ADK API caveat:** The exact integration pattern depends on the ADK version at implementation time. The key requirement is: ADK handles `POST /tasks/send`, `GET /tasks/{task_id}`, and SSE streaming routes for external callers, while existing routes (`POST /verify`, `GET /verify/*`, etc.) remain unchanged. Check ADK's documentation for the current mounting/integration API.

**Route conflict prevention:** ADK's `POST /tasks/send` and the data agents' `POST /tasks/send` use the same path but on different ports. There is no conflict — the Orchestrator is at `:8000`, data agents are at `:8001–8004`. The Mortgage Platform calls `:8000/tasks/send` (ADK-handled); the Orchestrator calls `:800X/tasks/send` on data agents (plain httpx).

---

### Step 9.5 — Update Agent Card for UC-5

**File:** `agents/orchestrator/agent_card.json` — update

The Orchestrator's Agent Card must advertise the `verify_subject` skill so external orchestrators can discover it.

**Current state:** The Agent Card already exists but may only list internal skills. Ensure it includes:

```json
{
  "name": "VerifyIQ Orchestrator",
  "url": "http://orchestrator:8000",
  "version": "1.0.0",
  "skills": [
    {
      "id": "verify_subject",
      "name": "Subject Verification",
      "description": "Full credit, employment, and international verification with risk synthesis",
      "input_schema": {
        "type": "object",
        "properties": {
          "subject_name": { "type": "string" },
          "subject_id": { "type": "string" },
          "use_case": { "type": "string", "enum": ["mortgage", "rental", "auto", "hire"] },
          "has_foreign_addr": { "type": "boolean" },
          "consent": { "type": "boolean" }
        },
        "required": ["subject_name", "subject_id", "use_case", "has_foreign_addr", "consent"]
      }
    }
  ],
  "capabilities": {
    "streaming": true,
    "task_cancellation": true
  },
  "auth_schemes": ["bearer"]
}
```

The Mortgage Platform will fetch this card from `http://orchestrator:8000/.well-known/agent.json` to discover what VerifyIQ can do.

---

### Step 9.6 — Forward Internal SSE Events to External Caller

The Mortgage Platform expects progress events via ADK's SSE stream. The internal pipeline already emits events to `SSEStreamer`. Bridge these to ADK's `task_context.send_status_update()`.

**Two approaches:**

**Approach A — Periodic status polling (simpler):**

After starting `run_verification()`, poll `task_manager.get_verification_request()` in a loop and forward status changes to ADK. Less granular but straightforward.

```python
# In handle_verification_task, after starting pipeline:
while True:
    result = task_manager.get_verification_request(internal_task_id)
    if result["status"] in ("completed", "failed"):
        break
    await task_context.send_status_update(f"Status: {result['status']}")
    await asyncio.sleep(1)
```

**Approach B — Subscribe to internal SSE stream (richer):**

Consume events from `sse_streamer.stream()` and forward each one through ADK.

```python
# In handle_verification_task, after starting pipeline as background task:
pipeline_task = asyncio.create_task(
    run_verification(internal_task_id, correlation_id, body)
)

async for event_str in sse_streamer.stream(internal_task_id, correlation_id):
    # Parse the SSE event string back to a dict
    # Forward to ADK
    await task_context.send_status_update(event_str)

await pipeline_task  # ensure pipeline is done
```

**Recommendation:** Start with Approach A for simplicity. The Mortgage Platform just needs to know the pipeline is progressing and receive the final artifact. Upgrade to Approach B if richer streaming is desired.

---

### Step 9.7 — Auth for Inbound A2A Tasks

External callers must authenticate. The `POST /tasks/send` endpoint handled by ADK needs the same bearer token validation as other protected endpoints.

**Options:**
- If ADK supports auth middleware/hooks, configure it to validate the bearer token
- If ADK exposes the raw request, validate the token in `handle_verification_task` before processing
- As a fallback, add FastAPI middleware that checks the token on the `/tasks/send` path before ADK processes it

**Pseudo code (middleware approach):**

```python
from agents.shared.auth import get_auth_token

@app.middleware("http")
async def auth_middleware(request, call_next):
    if request.url.path == "/tasks/send" and request.method == "POST":
        expected = get_auth_token()
        if expected:
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or not secrets.compare_digest(auth[7:], expected):
                return JSONResponse(status_code=401, content={"detail": "Invalid token"})
    return await call_next(request)
```

---

## Part B — Mortgage Platform Service

### Step 9.8 — Service Structure

**Directory:** `mortgage-platform/` (at repo root, not inside `agents/`)

> The Mortgage Platform is deliberately outside `agents/` — it represents an external team's codebase. It has no access to `agents/shared/` at runtime (though it may reference A2A types for convenience during development).

```
mortgage-platform/
├── Dockerfile
├── requirements.txt
├── main.py              # ADK agent — discovers VerifyIQ, dispatches task, logs result
└── agent_card.json      # Mortgage Platform's own Agent Card
```

**Requirements:**
```
google-adk>=0.5.0
httpx>=0.28.0
pydantic>=2.0
```

---

### Step 9.9 — Mortgage Platform Agent Card

**File:** `mortgage-platform/agent_card.json`

The Mortgage Platform advertises its own capabilities (loan origination pipeline). It consumes VerifyIQ as a sub-agent — it doesn't need to advertise verification skills.

```json
{
  "name": "Mortgage Platform Orchestrator",
  "url": "http://mortgage-platform:9000",
  "version": "1.0.0",
  "description": "Full loan origination pipeline — document collection, appraisal, underwriting, verification, rate locking",
  "skills": [
    {
      "id": "loan_origination",
      "name": "Mortgage Loan Origination",
      "description": "End-to-end mortgage loan processing"
    }
  ],
  "capabilities": {
    "streaming": true
  }
}
```

---

### Step 9.10 — Mortgage Platform Main Logic

**File:** `mortgage-platform/main.py`

This is a minimal service (~60–100 lines) that demonstrates the external caller pattern:

1. On startup, discover VerifyIQ's Agent Card
2. Expose a trigger endpoint (or run automatically on startup)
3. Dispatch an A2A task to VerifyIQ
4. Stream progress events
5. Log the final result

**Pseudo code:**

```python
# mortgage-platform/main.py

import asyncio
import json
import os
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager

VERIFYIQ_URL = os.environ.get("VERIFYIQ_URL", "http://orchestrator:8000")
AUTH_TOKEN = os.environ.get("VERIFYIQ_AUTH_TOKEN")

# Sample mortgage subject for UC-5 demo
SAMPLE_SUBJECT = {
    "subject_name": "David Kim",
    "subject_id": "SIM-KR-2018",
    "use_case": "mortgage",
    "has_foreign_addr": True,
    "consent": True,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup: discover VerifyIQ, run a verification, log results."""
    # Give the orchestrator time to start
    await asyncio.sleep(5)
    await run_demo()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "healthy", "service": "mortgage-platform", "port": 9000}


@app.get("/.well-known/agent.json")
def agent_card():
    with open("agent_card.json") as f:
        return json.load(f)


@app.post("/trigger")
async def trigger_verification():
    """Manually trigger a verification (alternative to auto-run on startup)."""
    result = await run_demo()
    return result


async def run_demo():
    """Discover VerifyIQ, dispatch a verification task, stream progress, log result."""
    headers = {}
    if AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1. Discover VerifyIQ Agent Card
        print("[Mortgage Platform] Discovering VerifyIQ...")
        card_resp = await client.get(f"{VERIFYIQ_URL}/.well-known/agent.json")
        card = card_resp.json()
        print(f"[Mortgage Platform] Found: {card['name']}")

        # Verify it has the skill we need
        skill_ids = [s["id"] if isinstance(s, dict) else s for s in card.get("skills", [])]
        if "verify_subject" not in skill_ids:
            print("[Mortgage Platform] ERROR: VerifyIQ does not advertise verify_subject skill")
            return {"error": "skill not found"}

        # 2. Dispatch A2A task
        # Option A: Use ADK client
        # Option B: Use plain httpx (simpler for demo)
        print("[Mortgage Platform] Dispatching verification task...")

        # If using POST /tasks/send (ADK endpoint):
        task_payload = {
            "task_id": "mp-task-001",
            "skill": "verify_subject",
            "input": SAMPLE_SUBJECT,
        }
        resp = await client.post(
            f"{VERIFYIQ_URL}/tasks/send",
            json=task_payload,
            headers=headers,
        )
        task_result = resp.json()
        task_id = task_result.get("task_id", task_payload["task_id"])
        print(f"[Mortgage Platform] Task accepted: {task_id}")

        # 3. Stream progress via SSE
        # Connect to VerifyIQ's SSE stream for this task
        print("[Mortgage Platform] Connecting to SSE stream...")
        async with client.stream(
            "GET",
            f"{VERIFYIQ_URL}/tasks/{task_id}/stream",
            headers=headers,
        ) as stream:
            async for line in stream.aiter_lines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    event_type = data.get("event_type", "unknown")
                    print(f"[Mortgage Platform] SSE: {event_type} — {data.get('message', '')}")
                    if event_type in ("completed", "failed"):
                        break

        # 4. Fetch final result
        print("[Mortgage Platform] Fetching final result...")
        status_resp = await client.get(
            f"{VERIFYIQ_URL}/tasks/{task_id}",
            headers=headers,
        )
        final = status_resp.json()
        print(f"[Mortgage Platform] Final status: {final.get('status')}")
        print(f"[Mortgage Platform] Decision: {json.dumps(final.get('artifact'), indent=2)}")

        return final
```

**Key design notes:**

- The Mortgage Platform **never imports** from `agents/shared/` or any VerifyIQ internal module. It only knows the A2A protocol: Agent Card discovery, `POST /tasks/send`, SSE streaming, `GET /tasks/{id}`.
- It uses plain `httpx` for the A2A calls. Alternatively, it could use ADK's A2A client — either works. Plain httpx makes the protocol explicit and educational.
- The demo runs automatically on startup (via `lifespan`) and can also be triggered manually via `POST /trigger`.
- The sample subject (`David Kim, SIM-KR-2018`) is a new persona distinct from the five CLI scenarios. Korean-American with foreign address — exercises the full four-agent pipeline.

---

### Step 9.11 — Mortgage Platform Dockerfile

**File:** `mortgage-platform/Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY mortgage-platform/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mortgage-platform/ .

EXPOSE 9000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]
```

> **Note:** Unlike the data agents, the Mortgage Platform does NOT copy `agents/shared/` into its container. It is an external service with no access to VerifyIQ internals.

---

## Part C — Docker Compose Updates

### Step 9.12 — Add Mortgage Platform Service

**File:** `docker-compose.yml` — add the Mortgage Platform service.

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

**Critical: the `profiles` key.**

The `profiles: [uc5]` setting means the Mortgage Platform does NOT start with `docker compose up`. It only starts when explicitly requested:

```bash
# Normal UC-1–4 workflow — Mortgage Platform does not start
$ docker compose up -d

# UC-5 demo — include the Mortgage Platform
$ docker compose --profile uc5 up -d

# Or start it individually after the stack is up
$ docker compose --profile uc5 up mortgage-platform -d
```

This prevents the Mortgage Platform from auto-triggering verifications every time you restart the stack.

---

## Part D — Orchestrator: A2A Callee Endpoints

### Step 9.13 — Decide: ADK-Managed vs. Hand-Rolled A2A Endpoints

At implementation time, you'll face a practical decision: does ADK's current API integrate cleanly with the existing FastAPI app, or does it fight it?

**If ADK integrates well:** Use `AgentExecutor` as described in Steps 9.3–9.4. ADK manages `POST /tasks/send`, `GET /tasks/{id}`, `GET /tasks/{id}/stream`, and task lifecycle.

**If ADK fights the existing app:** Hand-roll the A2A callee endpoints using plain FastAPI. This is more code but avoids framework conflicts. The learning value is actually higher — you implement the protocol yourself.

**Hand-rolled fallback (pseudo code):**

```python
# agents/orchestrator/__init__.py — additional routes for A2A callee

from pydantic import BaseModel

class InboundA2ATask(BaseModel):
    """Task from an external orchestrator (Mortgage Platform)."""
    task_id: str
    skill: str
    input: dict

# In-memory store for external task state
external_tasks: dict[str, dict] = {}


@app.post("/tasks/send")
async def a2a_tasks_send(task: InboundA2ATask, _token: str = Depends(require_auth)):
    """Receive an A2A task from an external orchestrator.

    Immediately returns the task_id. Pipeline runs in background.
    External caller polls GET /tasks/{id} or connects to SSE for progress.
    """
    # Map external task to internal verification request
    body = VerificationRequest(**task.input)
    internal_task_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())

    # Track the mapping: external task_id → internal task_id
    external_tasks[task.task_id] = {
        "status": "working",
        "internal_task_id": internal_task_id,
        "correlation_id": correlation_id,
        "artifact": None,
    }

    task_manager.create_verification_request(internal_task_id, correlation_id, body)

    # Run pipeline in background
    asyncio.create_task(
        _run_and_update_external(task.task_id, internal_task_id, correlation_id, body)
    )

    return {"task_id": task.task_id, "status": "working"}


async def _run_and_update_external(
    external_task_id: str,
    internal_task_id: str,
    correlation_id: str,
    body: VerificationRequest,
) -> None:
    """Run pipeline and update external task state when done."""
    await run_verification(internal_task_id, correlation_id, body)

    result = task_manager.get_verification_request(internal_task_id)
    if result and result["status"] == "completed":
        full = task_manager.get_full_verification(internal_task_id)
        synthesis = next(
            (t for t in full["agent_tasks"] if t["agent_name"] == "synthesis"), None
        )
        external_tasks[external_task_id].update({
            "status": "completed",
            "artifact": synthesis["artifact"] if synthesis else None,
        })
    else:
        external_tasks[external_task_id]["status"] = "failed"


@app.get("/tasks/{task_id}")
def get_external_task(task_id: str):
    """A2A task status — polled by external orchestrators."""
    if task_id in external_tasks:
        return external_tasks[task_id]

    # Fallback: check internal task store (for data agent polling)
    # This is the existing behavior for GET /tasks/{task_id} if it existed
    raise HTTPException(404, "Task not found")


@app.get("/tasks/{task_id}/stream")
async def stream_external_task(task_id: str):
    """SSE stream for external orchestrators watching a task."""
    if task_id not in external_tasks:
        raise HTTPException(404, "Task not found")

    internal_task_id = external_tasks[task_id]["internal_task_id"]
    correlation_id = external_tasks[task_id]["correlation_id"]

    # Reuse the existing SSE streamer
    async def event_stream():
        async for event in sse_streamer.stream(internal_task_id, correlation_id):
            yield event

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
```

**Recommendation:** Try ADK first (Steps 9.3–9.4). If it doesn't integrate cleanly within 1–2 hours, fall back to the hand-rolled approach above. The hand-rolled version is ~60 lines and gives full control. Document which approach you chose and why.

---

## Part D — CLI Update

### Step 9.14 — Enable the `mortgage-platform` Scenario

**File:** `cli/verifyiq_cli/commands/run.py`

The `mortgage-platform` scenario currently prints "not yet implemented". Update it to either:

**Option A — Trigger the Mortgage Platform via its API:**

```python
if scenario == "mortgage-platform":
    # Call the Mortgage Platform's trigger endpoint
    mp_url = os.environ.get("MORTGAGE_PLATFORM_URL", "http://localhost:9000")
    response = httpx.post(f"{mp_url}/trigger", headers=auth_headers)
    console.print(response.json())
    return
```

**Option B — Instruct the user to watch Docker logs:**

```python
if scenario == "mortgage-platform":
    console.print(
        "[bold]UC-5: Mortgage Platform → VerifyIQ[/bold]\n\n"
        "The Mortgage Platform runs as a separate Docker service.\n"
        "Start it and watch the logs:\n\n"
        "  docker compose --profile uc5 up mortgage-platform\n\n"
        "The platform will discover VerifyIQ, dispatch a verification task,\n"
        "stream progress events, and log the final decision."
    )
    return
```

**Recommendation:** Option A is more satisfying as a demo — one command does everything. But it requires the Mortgage Platform service to be running. Include a clear error message if it's not reachable.

**File:** `cli/verifyiq_cli/scenarios.py`

Add the UC-5 scenario payload (even though the CLI doesn't submit it directly to the Orchestrator — the Mortgage Platform does):

```python
"mortgage-platform": {
    "subject_name": "David Kim",
    "subject_id": "SIM-KR-2018",
    "use_case": "mortgage",
    "has_foreign_addr": True,
    "consent": True,
}
```

---

## Part E — UC-5 Tests

### Step 9.15 — Test File

**File:** `tests/test_phase9_uc5.py`

UC-5 tests verify the full external-caller round-trip. These are integration tests that require all services + the Mortgage Platform to be running.

---

### Test Setup

```python
import os
import httpx
import pytest
import asyncio

ORCHESTRATOR_URL = "http://localhost:8000"
MORTGAGE_PLATFORM_URL = "http://localhost:9000"
AUTH_TOKEN = os.environ.get("VERIFYIQ_AUTH_TOKEN", "verifyiq-dev-token-2026")
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}
```

> **Note:** These tests require `docker compose --profile uc5 up -d` to include the Mortgage Platform.

---

### UC-5 Tests (6 tests)

**1. `test_orchestrator_agent_card_advertises_verify_subject`**

`GET /.well-known/agent.json` on the Orchestrator. Parse the response.

Assertions:
- Response is 200
- Skills list includes an entry with `id: "verify_subject"`
- `capabilities.streaming` is `true`

---

**2. `test_tasks_send_returns_task_id_immediately`**

`POST /tasks/send` to the Orchestrator with a valid verification payload and auth header.

Assertions:
- Response returns within 2 seconds (does not block on pipeline)
- Response contains `task_id`
- Response contains `status: "working"` (or equivalent acknowledgement)

---

**3. `test_tasks_get_returns_working_while_pipeline_runs`**

`POST /tasks/send`, then immediately `GET /tasks/{task_id}`.

Assertions:
- Status is `"working"` (pipeline hasn't finished yet)

---

**4. `test_tasks_get_returns_completed_with_artifact`**

`POST /tasks/send`, then poll `GET /tasks/{task_id}` until status is terminal (max 60 seconds).

Assertions:
- Final status is `"completed"`
- `artifact` is present and is a dict
- Artifact contains `decision` (one of: approve, review, decline)
- Artifact contains `confidence`, `risk_score`, `reasoning_summary`

---

**5. `test_sse_stream_delivers_events_to_external_caller`**

`POST /tasks/send`, then connect to `GET /tasks/{task_id}/stream`.

Assertions:
- At least 3 SSE events received before the stream ends
- Events include progress milestones (agents resolved, agent completed, etc.)
- Final event indicates completion or failure

> **Implementation note:** Use `httpx.AsyncClient.stream()` to consume the SSE stream. Parse `data:` lines as JSON.

---

**6. `test_correlation_id_propagates_to_internal_tasks`**

`POST /tasks/send`, wait for completion, then query the Orchestrator's internal state.

Two approaches:
- If `GET /tasks/{task_id}` exposes the `correlation_id` and `internal_task_id`, use `GET /verify/{internal_task_id}/full` to check
- Alternatively, use `GET /verify/history` to find the most recent request and verify its agent tasks all share the same `correlation_id`

Assertions:
- A verification request was created in the Orchestrator's DB
- All `agent_tasks` rows share the same `correlation_id`
- At least 3 agent tasks present (equifax, employment, synthesis — intl depends on input)

---

### Mortgage Platform Integration Test (1 test)

**7. `test_mortgage_platform_trigger_completes`**

`POST /trigger` on the Mortgage Platform service (port 9000).

Assertions:
- Response returns within 60 seconds
- Response contains a status of `"completed"`
- Response contains an artifact with a `decision`

> This is the full end-to-end demo: Mortgage Platform → VerifyIQ → data agents → synthesis → result back to Mortgage Platform.

---

## Running Tests

```bash
# Start the full stack including Mortgage Platform
$ docker compose --profile uc5 up --build -d
$ sleep 15

# Set auth token
$ export VERIFYIQ_AUTH_TOKEN=verifyiq-dev-token-2026

# Phase 9 tests only
$ pytest tests/test_phase9_uc5.py -v

# Full regression
$ pytest tests/ -v
```

**Common failure modes:**

| Symptom | Likely Cause |
|---|---|
| `POST /tasks/send` returns 404 | ADK routes not mounted; or hand-rolled endpoints not added |
| `POST /tasks/send` returns 422 | Payload shape doesn't match what ADK/handler expects |
| Mortgage Platform can't discover Agent Card | `VERIFYIQ_URL` env var wrong; or Agent Card doesn't include `verify_subject` skill |
| Pipeline runs but external caller gets no SSE events | SSE stream not bridged from internal to external task |
| `correlation_id` not propagating | `a2a_handler` not generating/passing `correlation_id` to `run_verification` |
| Mortgage Platform starts but times out | Orchestrator not healthy yet; increase sleep or add retry logic in Mortgage Platform startup |
| ADK import errors | Version mismatch or dependency conflict; check `pip install` output |
| ADK and FastAPI route conflict | Both trying to handle the same path; check mounting strategy |
| Tests pass individually but fail together | External task store (in-memory dict) not isolated between tests |

---

## Commit

```bash
$ git add .
$ git commit -m "Phase 9: ADK server-side + Mortgage Platform UC-5"
```

---

## ✅ Phase 9 Verification Checklist

**ADK / A2A Callee (Orchestrator):**
- [ ] `POST /tasks/send` on Orchestrator accepts an A2A task and returns `task_id` immediately
- [ ] `GET /tasks/{task_id}` returns `"working"` while pipeline runs
- [ ] `GET /tasks/{task_id}` returns `"completed"` with `VerificationDecision` artifact after pipeline finishes
- [ ] `GET /tasks/{task_id}/stream` delivers SSE events to external caller
- [ ] `POST /tasks/send` without auth token returns 401
- [ ] `POST /tasks/send` with valid auth token succeeds
- [ ] Internal pipeline runs identically to UC-1–4 (same agents, same fan-out, same synthesis)
- [ ] `correlation_id` propagates from inbound task through all internal `agent_tasks` rows

**Agent Card:**
- [ ] `GET /.well-known/agent.json` on Orchestrator includes `verify_subject` skill
- [ ] `capabilities.streaming` is `true`
- [ ] `auth_schemes` includes `"bearer"`

**Mortgage Platform:**
- [ ] Service starts at `:9000` and responds to `GET /health`
- [ ] `GET /.well-known/agent.json` returns Mortgage Platform's Agent Card
- [ ] On startup (or via `POST /trigger`), discovers VerifyIQ Agent Card
- [ ] Dispatches A2A task to VerifyIQ's `POST /tasks/send`
- [ ] Receives SSE progress events
- [ ] Logs the final `VerificationDecision` artifact
- [ ] Does NOT import from `agents/shared/` — protocol-only integration

**Docker Compose:**
- [ ] `docker compose up -d` does NOT start the Mortgage Platform (profiles)
- [ ] `docker compose --profile uc5 up -d` starts all services including Mortgage Platform
- [ ] Mortgage Platform depends on Orchestrator being healthy
- [ ] Port 9000 exposed and accessible

**CLI:**
- [ ] `verifyiq run mortgage-platform` either triggers the platform or shows instructions
- [ ] All other scenarios (UC-1 through UC-4) still work unchanged

**Existing Tests:**
- [ ] `pytest tests/ -v` — all Phase 3, 5, 6, 7, and 8 tests pass (no regressions)
- [ ] `pytest tests/test_phase9_uc5.py -v` — all 7 UC-5 tests pass

**Demo Flow:**
- [ ] Start full stack: `docker compose --profile uc5 up --build -d`
- [ ] Watch Mortgage Platform logs: `docker compose logs -f mortgage-platform`
- [ ] See: discovery → task dispatch → SSE progress → final decision
- [ ] Verify the Mortgage Platform has NO knowledge of Equifax, LangGraph, or OpenRouter
- [ ] `docker compose logs | grep <correlation_id>` traces the full request across all containers

---

*Document: VerifyIQ Implementation Guide | Phase 9 | Companion to Spec v1.7*
