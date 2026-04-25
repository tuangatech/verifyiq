# VerifyIQ — Implementation Guide
## Phase 7 — CLI (`verifyiq-cli`)
*Companion to Project Specification v1.7*

---

## How to Use This Guide

This guide describes **what to build, why, and how to verify it**. It gives Claude Code enough context to generate correct implementations without prescribing every line. Focus is on intent, structure, constraints, and verification — not code.

**Conventions:**

- `$` lines are terminal commands you run directly
- ✅ Checklist items at the end of the phase must all pass before proceeding

---

## Phase 7 Overview

**Goal:** A fully usable CLI tool that replaces `curl` + Swagger for interacting with VerifyIQ. Six pre-defined scenarios cover all use cases (UC-1a through UC-5). Live SSE rendering shows the pipeline executing in real time. Agent network, request history, and artifact inspection are available as rich terminal views.

Phase 7 has two parts:
1. **Two new Orchestrator endpoints** (`GET /verify/history`, `GET /verify/{task_id}/full`) — the CLI needs these but they don't exist yet.
2. **The CLI package** (`cli/`) — a standalone Python package using typer + rich + httpx.

**Estimated time:** 3–4 hours

**Prerequisite:** Phase 6 complete. `pytest tests/ -v` passes all Phase 3, 5, and 6 tests.

---

## Part A — Orchestrator Endpoints

### Step 7.1 — Add `get_verification_history` to `TaskManager`

**File:** `agents/orchestrator/db.py` — add method.

**Purpose:** The CLI `verifyiq history` command needs a list of past verification requests. The Orchestrator does not currently expose this.

**Method to add:**

`get_verification_history(limit: int = 20) -> list[dict]`:
- SELECT from `verification_requests` ordered by `created_at DESC`
- Apply `LIMIT ?` from the parameter
- Return list of dicts: `task_id`, `correlation_id`, `subject_name`, `use_case`, `status`, `decision`, `created_at`, `completed_at`
- No offset/cursor for now — simple limit is sufficient for a demo CLI

**Key constraint:** Return the same column dict format as `get_verification_request` for consistency. The CLI models will subset the fields.

---

### Step 7.2 — Add `get_full_verification` to `TaskManager`

**File:** `agents/orchestrator/db.py` — add method.

**Purpose:** The CLI `verifyiq inspect <task_id>` command needs the full artifact dump — the verification request, all agent task rows, and all SSE events — in a single response.

**Method to add:**

`get_full_verification(task_id: str) -> dict | None`:
- Call `get_verification_request(task_id)` → return `None` if not found
- Call `get_agent_tasks(task_id)` → list of agent task dicts
- Call `get_sse_events(correlation_id)` → list of SSE event dicts (use the `correlation_id` from the verification request)
- Parse JSON strings back to dicts for `artifact`, `error`, `input`, and `payload` fields so the response is clean nested JSON (not escaped strings)
- Return: `{ "request": {...}, "agent_tasks": [...], "sse_events": [...] }`

**JSON parsing note:** The DB stores `artifact`, `error`, `input` (on agent_tasks) and `payload` (on sse_events) as JSON strings. Parse them with `json.loads()` before returning so the API response has proper nested objects, not escaped strings. Guard with `try/except` for null or invalid JSON — return the raw value as-is on failure.

---

### Step 7.3 — Add Endpoints to Orchestrator

**File:** `agents/orchestrator/__init__.py` — add two endpoints.

**Endpoint 1: `GET /verify/history`**

Query param: `limit` (int, default 20, range 1–100).

Returns a JSON array of history items from `task_manager.get_verification_history(limit)`.

> ⚠️ **Route ordering matters.** FastAPI matches routes top-to-bottom. `GET /verify/history` must be registered **before** `GET /verify/{task_id}`, otherwise FastAPI will interpret `"history"` as a `task_id` parameter. Move or add the `history` route above the `{task_id}` route.

**Endpoint 2: `GET /verify/{task_id}/full`**

Returns the full artifact dump from `task_manager.get_full_verification(task_id)`. Returns 404 if task not found.

**Response models (add to `agents/orchestrator/models.py`):**

`HistoryItem`:
```
task_id: str
correlation_id: str
subject_name: str
use_case: str
status: str
decision: Optional[str]
created_at: str
completed_at: Optional[str]
```

`FullVerificationResponse`:
```
request: dict
agent_tasks: list[dict]
sse_events: list[dict]
```

These response models are for documentation and validation on the Orchestrator side. The CLI will have its own lightweight models.

---

### Step 7.4 — Smoke Test New Endpoints

First, make sure there is at least one completed verification in the DB:

```bash
$ docker compose down && docker compose up --build -d

# Wait for all services to register
$ sleep 5

# Submit a request
$ curl -s -X POST http://localhost:8000/verify \
    -H "Content-Type: application/json" \
    -d '{"subject_name":"Smoke Test","subject_id":"S-smoke","use_case":"mortgage","has_foreign_addr":true,"consent":true}' \
    | python -m json.tool
# Note the task_id

# Wait for completion
$ sleep 15

# Test history
$ curl -s http://localhost:8000/verify/history | python -m json.tool
# Expect: array with at least 1 item

# Test history with limit
$ curl -s "http://localhost:8000/verify/history?limit=5" | python -m json.tool

# Test full (replace TASK_ID with actual value)
$ curl -s http://localhost:8000/verify/TASK_ID/full | python -m json.tool
# Expect: { "request": {...}, "agent_tasks": [...], "sse_events": [...] }
```

---

## Part B — CLI Package

### Step 7.5 — Package Structure

Create the `cli/` directory at the repository root with this structure:

```
cli/
├── pyproject.toml
└── verifyiq_cli/
    ├── __init__.py
    ├── main.py                 # typer app entry point
    ├── client.py               # httpx wrapper
    ├── models.py               # lightweight response models
    ├── scenarios.py            # scenario name → request payload mapping
    ├── display.py              # rich rendering (tables, panels, SSE events)
    └── commands/
        ├── __init__.py
        ├── run.py              # verifyiq run <scenario>
        ├── agents.py           # verifyiq agents
        ├── history.py          # verifyiq history
        └── inspect_cmd.py      # verifyiq inspect <task_id>
```

> ⚠️ **Name the inspect command file `inspect_cmd.py`**, not `inspect.py`. Python has a built-in `inspect` module — naming the file `inspect.py` will shadow it and cause confusing import errors.

> ⚠️ **No path dependency on `agents/shared/`.** The CLI is a standalone host-side package. It defines its own lightweight response models in `cli/verifyiq_cli/models.py`. It never imports from `agents/`.

---

### Step 7.6 — `pyproject.toml`

**File:** `cli/pyproject.toml`

Defines the `verifyiq-cli` package. Key fields:

```toml
[project]
name = "verifyiq-cli"
version = "0.1.0"
description = "CLI for VerifyIQ verification platform"
requires-python = ">=3.11"
dependencies = [
    "typer>=0.15.0",
    "rich>=13.0",
    "httpx>=0.27.0",
    "pydantic>=2.0",
]

[project.scripts]
verifyiq = "verifyiq_cli.main:app"
```

The `[project.scripts]` entry creates the `verifyiq` command when installed with `uv pip install -e cli/`.

---

### Step 7.7 — `client.py` — HTTP Client Wrapper

**File:** `cli/verifyiq_cli/client.py`

**Purpose:** All HTTP communication with the Orchestrator lives here. Commands import the client, not `httpx` directly. This isolates HTTP concerns (base URL, timeouts, error handling) in one place.

**Configuration:** `VERIFYIQ_URL` env var, default `http://localhost:8000`.

**Class: `VerifyIQClient`**

Wraps `httpx.Client` (synchronous — typer commands are sync functions; async is unnecessary for a CLI that makes 1–2 HTTP calls per command).

**Methods:**

| Method | HTTP call | Returns |
|---|---|---|
| `submit_verification(payload: dict) -> dict` | `POST /verify` | `{ task_id, correlation_id, stream_url }` |
| `get_status(task_id: str) -> dict` | `GET /verify/{task_id}` | Task status response |
| `get_full(task_id: str) -> dict` | `GET /verify/{task_id}/full` | Full artifact dump |
| `get_history(limit: int) -> list[dict]` | `GET /verify/history?limit=N` | List of history items |
| `list_agents() -> list[dict]` | `GET /agents` | List of registered agents |
| `stream_events(task_id: str) -> Generator[dict, None, None]` | `GET /verify/{task_id}/stream` | Yields parsed SSE event dicts |

**SSE streaming:** The `stream_events` method uses `httpx.Client.stream("GET", url)` and yields parsed event dicts. It reads lines, filters for `data:` prefix, parses JSON, and yields. Stops on `event_type == "completed"` or `"failed"`.

**Error handling:** Raise `typer.Exit(code=1)` with a `rich.console.print` error message on connection errors or non-2xx responses. Use `rich.console.Console(stderr=True)` for error output so it doesn't interfere with `--json` piping.

**Timeout:** 60 seconds for SSE streaming (pipeline can take 5–15 seconds); 10 seconds for all other calls.

---

### Step 7.8 — `models.py` — CLI Response Models

**File:** `cli/verifyiq_cli/models.py`

**Purpose:** Lightweight Pydantic models for deserializing Orchestrator responses. These are **not** copies of `agents/shared/schemas.py` — they are simpler, flatter models tailored to what the CLI needs to display.

**Models:**

`VerifyResponse`:
```python
task_id: str
correlation_id: str
stream_url: str
```

`TaskStatus`:
```python
task_id: str
correlation_id: str
status: str
decision: Optional[str] = None
created_at: str
completed_at: Optional[str] = None
```

`HistoryItem`:
```python
task_id: str
subject_name: str
use_case: str
status: str
decision: Optional[str] = None
created_at: str
```

`AgentInfo`:
```python
name: str
url: str
skills: list[str] | str  # Registry returns JSON string or list depending on format
health: str
avg_latency_ms: Optional[float] = None
last_seen: Optional[str] = None
```

> These models use `Optional` generously — the CLI must not crash if the Orchestrator returns null fields for in-progress or failed requests.

---

### Step 7.9 — `scenarios.py` — Pre-Defined Subject Payloads

**File:** `cli/verifyiq_cli/scenarios.py`

**Purpose:** Maps scenario names to pre-defined `VerificationRequest` payloads. Each scenario exercises a specific use case and agent combination.

**Scenarios:**

| Scenario name | `subject_name` | `subject_id` | `use_case` | `has_foreign_addr` | Notes |
|---|---|---|---|---|---|
| `mortgage-intl` | Nguyen Minh Tuan | SIM-VN-2019 | `mortgage` | `true` | UC-1a: Vietnamese-American, intl data available |
| `mortgage-domestic` | Sarah Johnson | SIM-US-1985 | `mortgage` | `true` | UC-1b: US-born, intl dispatched but returns unavailable |
| `rental` | Marcus Williams | SIM-US-1992 | `rental` | `false` | UC-2: Intl skipped entirely |
| `auto` | Jennifer Chen | SIM-US-1988 | `auto` | `false` | UC-3: DTI-weighted synthesis |
| `hire` | Raj Patel | SIM-IN-2020 | `hire` | `true` | UC-4: Equifax skipped, intl required |
| `mortgage-platform` | N/A | N/A | N/A | N/A | UC-5: exits with "not yet implemented" |

**Implementation:** A `SCENARIOS` dict mapping scenario name to payload dict. A `get_scenario(name: str) -> dict` function that returns the payload or raises `typer.BadParameter` with a message listing available scenarios.

**Subject IDs:** Use memorable simulated IDs (not random UUIDs) so demo output is readable and comparable across runs.

**`consent`:** Always `true` for all scenarios.

> ⚠️ `mortgage-domestic` has `has_foreign_addr: true` — the officer checked the box, but the International Agent finds nothing and returns `data_availability: unavailable`. This is the UC-1b graceful degradation demo. It is distinct from `rental` where `has_foreign_addr: false` means the International Agent is never invoked.

---

### Step 7.10 — `display.py` — Rich Rendering

**File:** `cli/verifyiq_cli/display.py`

**Purpose:** All rich output formatting lives here. Commands call display functions; they don't construct `Table` or `Panel` objects directly. This keeps command files focused on orchestration logic.

**Functions:**

**`render_agents_table(agents: list[dict]) -> None`**

Renders a `rich.table.Table` with columns: Name, URL, Skills, Health, Avg Latency (ms), Last Seen. Skills column should format the JSON list nicely (comma-separated, not raw JSON).

**`render_history_table(items: list[dict]) -> None`**

Renders a `rich.table.Table` with columns: Task ID (full UUID — needed for copy-paste into `verifyiq inspect`), Subject, Use Case, Status, Decision, Created At. Color-code the status column: green for `completed`, red for `failed`, yellow for `working`/`submitted`.

**`render_sse_event(event: dict) -> None`**

Renders a single SSE event as a live progress line. Each event type gets a distinct visual treatment:

| Event type | Display |
|---|---|
| `agents_resolved` | `[bold]Agents resolved:[/bold] equifax, employment, intl` |
| `agent_started` | `  → Starting [agent_name]...` |
| `agent_completed` | `  ✓ [agent_name] completed` (green) |
| `agent_failed` | `  ✗ [agent_name] failed: [error]` (red) |
| `agent_skipped` | `  ○ [agent_name] skipped: [reason]` (dim) |
| `synthesis_started` | `  → Running risk synthesis...` |
| `synthesis_completed` | `  ✓ Synthesis: [decision] (confidence: [confidence])` (green) |
| `completed` | `[bold green]Pipeline completed[/bold green] — decision: [decision]` |
| `failed` | `[bold red]Pipeline failed[/bold red]: [error]` |

Use `rich.console.Console()` for output. Each event is one `console.print()` call — no `Live` context needed since events arrive sequentially.

**`render_full_report(data: dict) -> None`**

Renders the full artifact dump from `GET /verify/{task_id}/full` as rich panels:

1. **Request Summary** panel — subject name, use case, status, decision, correlation_id, timestamps
2. **Agent Tasks** panel per agent — agent name, status, timing (started_at → ended_at), artifact summary or error. Color-code by status.
3. **Decision** panel (if completed) — decision, confidence, risk score, decision factors, risk flags, reasoning summary. Pull these from the synthesis agent task's artifact.

Use `rich.panel.Panel` with titles and `rich.table.Table` for structured data within panels.

**`render_json(data: Any) -> None`**

For `--json` flag: pretty-print raw JSON to stdout using `rich.print_json` or `json.dumps(data, indent=2)`.

---

### Step 7.11 — `main.py` — Typer App Entry Point

**File:** `cli/verifyiq_cli/main.py`

**Purpose:** Defines the top-level `typer.Typer()` app and registers all subcommands.

```python
import typer
from .commands import run, agents, history, inspect_cmd

app = typer.Typer(
    name="verifyiq",
    help="VerifyIQ — Multi-Agent Verification Platform CLI",
    no_args_is_help=True,
)

app.command(name="run")(run.run_scenario)
app.command(name="agents")(agents.list_agents)
app.command(name="history")(history.show_history)
app.command(name="inspect")(inspect_cmd.inspect_task)
```

> `no_args_is_help=True` — running `verifyiq` with no arguments shows the help text.

---

### Step 7.12 — `commands/run.py` — `verifyiq run <scenario>`

**File:** `cli/verifyiq_cli/commands/run.py`

**Purpose:** The primary user command. Submits a scenario, streams SSE events live, and prints a summary.

**Signature:**
```python
def run_scenario(
    scenario: str = typer.Argument(..., help="Scenario name (e.g. mortgage-intl, rental, auto, hire)"),
    no_watch: bool = typer.Option(False, "--no-watch", help="Skip live SSE streaming; poll for result instead"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON instead of rich panels"),
) -> None:
```

**Logic:**

1. Look up scenario in `scenarios.get_scenario(scenario)`. Exit with error + list of valid scenarios if not found.

2. **Special case:** If `scenario == "mortgage-platform"`, print `"UC-5 (Mortgage Platform) is not yet implemented. Available in Phase 9."` and exit with code 0. This is a placeholder — the scenario requires the Mortgage Platform service which is Phase 9.

3. Create `VerifyIQClient`. Call `client.submit_verification(payload)`. Print the `task_id` and `correlation_id`.

4. **If `--no-watch`:** Poll `client.get_status(task_id)` every 1 second until status is terminal (`completed` or `failed`). Print final status and decision.

5. **If watching (default):** Iterate over `client.stream_events(task_id)`. For each event, call `display.render_sse_event(event)`. After the stream ends (terminal event received), fetch the final status with `client.get_status(task_id)` and print a one-line summary.

6. **If `--json`:** After the pipeline completes (whether via streaming or polling), fetch `client.get_full(task_id)` and call `display.render_json(data)`.

**Error handling:** If the Orchestrator is unreachable, print a clear message: `"Cannot connect to Orchestrator at {url}. Is Docker Compose running?"` and exit code 1.

---

### Step 7.13 — `commands/agents.py` — `verifyiq agents`

**File:** `cli/verifyiq_cli/commands/agents.py`

**Signature:**
```python
def list_agents(
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
```

**Logic:**

1. Create `VerifyIQClient`. Call `client.list_agents()`.
2. If `--json`: call `display.render_json(agents)`.
3. Otherwise: call `display.render_agents_table(agents)`.
4. If no agents registered: print `"No agents registered. Is the stack running?"`.

---

### Step 7.14 — `commands/history.py` — `verifyiq history`

**File:** `cli/verifyiq_cli/commands/history.py`

**Signature:**
```python
def show_history(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of recent requests to show"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
```

**Logic:**

1. Create `VerifyIQClient`. Call `client.get_history(limit)`.
2. If `--json`: call `display.render_json(items)`.
3. Otherwise: call `display.render_history_table(items)`.
4. If no items: print `"No verification requests found."`.

---

### Step 7.15 — `commands/inspect_cmd.py` — `verifyiq inspect <task_id>`

**File:** `cli/verifyiq_cli/commands/inspect_cmd.py`

**Signature:**
```python
def inspect_task(
    task_id: str = typer.Argument(..., help="Task ID from a previous verification request"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
```

**Logic:**

1. Create `VerifyIQClient`. Call `client.get_full(task_id)`.
2. If 404: print `"Task not found: {task_id}"` and exit code 1.
3. If `--json`: call `display.render_json(data)`.
4. Otherwise: call `display.render_full_report(data)`.

**Tip for users:** `verifyiq history` shows task IDs; copy one and pass it to `verifyiq inspect`.

---

### Step 7.16 — Install and Smoke Test

```bash
# Create and activate a virtual environment (run from repo root)
$ uv venv
$ source .venv/Scripts/activate    # Windows (Git Bash); use .venv/bin/activate on macOS/Linux

# Install the CLI package in editable mode
$ uv pip install -e cli/

# Verify the command is available
$ verifyiq --help
# Expect: help text with run, agents, history, inspect commands

# Rebuild Docker services with new endpoints
$ docker compose down && docker compose up --build -d
$ sleep 10

# Test agents command
$ verifyiq agents
# Expect: rich table with 5 agents (orchestrator, equifax, employment, intl, synthesis)

# Test run with live streaming
$ verifyiq run mortgage-intl
# Expect: SSE events rendered line-by-line, ending with pipeline completed

# Test run with --no-watch
$ verifyiq run rental --no-watch
# Expect: polling dots or status updates, then final decision

# Test run with --json
$ verifyiq run auto --json
# Expect: raw JSON artifact dump

# Test history
$ verifyiq history
# Expect: table with the requests we just submitted

# Test inspect (copy a task_id from history output)
$ verifyiq inspect <task_id>
# Expect: rich panels with request details, agent tasks, decision

# Test mortgage-platform placeholder
$ verifyiq run mortgage-platform
# Expect: "UC-5 (Mortgage Platform) is not yet implemented. Available in Phase 9."

# Test invalid scenario
$ verifyiq run nonexistent
# Expect: error message with list of valid scenarios
```

---

### Step 7.17 — Verify All Use Cases

Run each scenario and verify the expected agent invocation pattern:

```bash
# UC-1a: All four agents
$ verifyiq run mortgage-intl
# Expect: equifax ✓, employment ✓, intl ✓, synthesis ✓

# UC-1b: Intl dispatched but returns unavailable
$ verifyiq run mortgage-domestic
# Expect: equifax ✓, employment ✓, intl ✓ (completed, unavailable), synthesis ✓

# UC-2: Intl skipped
$ verifyiq run rental
# Expect: equifax ✓, employment ✓, intl ○ skipped, synthesis ✓

# UC-3: DTI-weighted synthesis
$ verifyiq run auto
# Expect: equifax ✓, employment ✓, intl ○ skipped, synthesis ✓

# UC-4: Equifax skipped
$ verifyiq run hire
# Expect: equifax ○ skipped, employment ✓, intl ✓, synthesis ✓
```

---

## Phase 7 Tests

**File:** `tests/test_phase7_cli.py`

Use the same helper patterns as Phase 5 and 6 tests: `_verify_request()`, `_submit_and_wait()`, `orchestrator_client` fixture from `conftest.py`.

Phase 7 tests focus on two areas:
1. **New Orchestrator endpoints** — `GET /verify/history` and `GET /verify/{task_id}/full`
2. **End-to-end use case verification** — all six scenarios produce correct results through the full pipeline

---

### Endpoint Tests (4 tests)

---

**1. `test_history_returns_recent_requests`**

Submit two verification requests with different use cases (`mortgage` + `rental`). Wait for both to complete. Call `GET /verify/history?limit=10`.

Assertions:
- Response is 200 and is a JSON list
- List has at least 2 items
- Each item has keys: `task_id`, `subject_name`, `use_case`, `status`, `created_at`
- Items are ordered by `created_at` descending (most recent first)
- The two just-submitted requests appear in the list

---

**2. `test_history_respects_limit`**

Submit 3 requests. Wait for all to complete. Call `GET /verify/history?limit=2`.

Assertions:
- Exactly 2 items returned (not 3)

---

**3. `test_full_returns_complete_dump`**

Submit a `mortgage` + `has_foreign_addr=true` request. Wait for completion. Call `GET /verify/{task_id}/full`.

Assertions:
- Response is 200
- Response has keys: `request`, `agent_tasks`, `sse_events`
- `request` has `task_id`, `correlation_id`, `status`, `decision`
- `agent_tasks` is a list with 4 items (equifax, employment, intl, synthesis)
- Each agent task has `agent_name`, `status`, `artifact` or `error`
- `sse_events` is a list with at least 4 events
- Artifact fields on completed agent tasks are parsed dicts (not JSON strings)

---

**4. `test_full_returns_404_for_unknown_task`**

Call `GET /verify/nonexistent-id/full`.

Assertions:
- Response is 404

---

### End-to-End Use Case Tests (5 tests)

These tests run through the Orchestrator HTTP API (not the CLI binary) to verify each use case produces the correct agent invocation pattern and decision.

---

**5. `test_uc1a_mortgage_intl_all_agents`** — UC-1a: Full four-agent pipeline.

Submit `use_case="mortgage"`, `has_foreign_addr=True`. Wait for completion. Get agent tasks via `GET /verify/{task_id}/full`.

Assertions:
- Status `"completed"`, decision is one of `["approve", "review", "decline"]`
- 4 agent tasks present: equifax, employment, intl, synthesis
- All 4 have status `"completed"`
- Intl artifact has `data_availability` field (either `"full"`, `"partial"`, or `"unavailable"`)
- Synthesis artifact has `decision`, `confidence`, `risk_score`, `decision_factors`, `reasoning_summary`

---

**6. `test_uc1b_mortgage_domestic_intl_unavailable`** — UC-1b: Intl dispatched, returns unavailable or completes.

Submit `use_case="mortgage"`, `has_foreign_addr=True`, `subject_name="Sarah Johnson"`. Wait for completion. Get full dump.

Assertions:
- Status `"completed"` — pipeline did not fail regardless of intl result
- Intl agent task has status `"completed"` (it was dispatched, not skipped)
- Decision is present
- All 4 agent tasks present with same `correlation_id`

> ⚠️ We cannot deterministically force the LLM to return `data_availability: unavailable` — the test verifies the pipeline completes regardless of what the International agent returns.

---

**7. `test_uc2_rental_intl_skipped`** — UC-2: Intl skipped entirely.

Submit `use_case="rental"`, `has_foreign_addr=False`. Wait for completion. Get full dump.

Assertions:
- Status `"completed"`
- Agent tasks: equifax=`completed`, employment=`completed`, intl=`skipped`, synthesis=`completed`
- Full dump `sse_events` contains an `agent_skipped` event for intl
- No `agent_started` event for intl in SSE events

---

**8. `test_uc3_auto_completes`** — UC-3: Auto loan.

Submit `use_case="auto"`, `has_foreign_addr=False`. Wait for completion. Get full dump.

Assertions:
- Status `"completed"`
- Agent tasks: equifax=`completed`, employment=`completed`, intl=`skipped`, synthesis=`completed`
- Synthesis artifact has `decision` and `reasoning_summary`

---

**9. `test_uc4_hire_equifax_skipped`** — UC-4: Equifax skipped, intl required.

Submit `use_case="hire"`, `has_foreign_addr=True`. Wait for completion. Get full dump.

Assertions:
- Status `"completed"`
- Agent tasks: equifax=`skipped`, employment=`completed`, intl=`completed`, synthesis=`completed`
- No `agent_started` event for equifax in SSE events

---

### Correlation ID Consistency Test (1 test)

---

**10. `test_correlation_id_consistent_in_full_dump`**

Submit any request. Wait for completion. Call `GET /verify/{task_id}/full`.

Assertions:
- `request.correlation_id` matches every `agent_tasks[].correlation_id`
- `request.correlation_id` matches every `sse_events[].correlation_id`
- No null `correlation_id` anywhere

---

## Running Tests

```bash
# Ensure services are running
$ docker compose up --build -d

# Phase 7 only
$ pytest tests/test_phase7_cli.py -v

# Full regression
$ pytest tests/ -v
```

**Common failure modes:**

| Symptom | Likely Cause |
|---|---|
| `GET /verify/history` returns 422 | Route ordering — `history` matched as `{task_id}` parameter; move route above `{task_id}` |
| `GET /verify/{task_id}/full` returns JSON strings for artifacts | `json.loads()` not applied in `get_full_verification` |
| CLI command not found after `uv pip install -e cli/` | Missing `[project.scripts]` in `pyproject.toml` |
| `verifyiq run` hangs | SSE stream never receives terminal event; check orchestrator logs for pipeline errors |
| `verifyiq agents` shows empty table | Services not registered; check `docker compose logs registry` |
| `ModuleNotFoundError: inspect` | Command file named `inspect.py` instead of `inspect_cmd.py` |
| History items not ordered | Missing `ORDER BY created_at DESC` in query |

---

## Commit

```bash
$ git add .
$ git commit -m "Phase 7: CLI (verifyiq-cli) + history/full endpoints"
```

---

## ✅ Phase 7 Verification Checklist

**Orchestrator Endpoints:**
- [ ] `GET /verify/history` returns a JSON list ordered by `created_at DESC`
- [ ] `GET /verify/history?limit=2` returns exactly 2 items
- [ ] `GET /verify/{task_id}/full` returns `request`, `agent_tasks`, `sse_events`
- [ ] `GET /verify/{task_id}/full` artifacts are parsed JSON objects, not escaped strings
- [ ] `GET /verify/unknown-id/full` returns 404

**CLI Installation:**
- [ ] `uv pip install -e cli/` completes without errors
- [ ] `verifyiq --help` shows all four commands
- [ ] `verifyiq run --help` shows `scenario`, `--no-watch`, `--json` options

**CLI Commands:**
- [ ] `verifyiq run mortgage-intl` — SSE events stream live, pipeline completes with decision
- [ ] `verifyiq run mortgage-domestic` — intl completes (unavailable or full), pipeline completes
- [ ] `verifyiq run rental` — intl skipped, pipeline completes
- [ ] `verifyiq run auto` — intl skipped, pipeline completes
- [ ] `verifyiq run hire` — equifax skipped, intl invoked, pipeline completes
- [ ] `verifyiq run mortgage-platform` — prints "not yet implemented" message
- [ ] `verifyiq run nonexistent` — prints error with valid scenario names
- [ ] `verifyiq run rental --no-watch` — polls and shows final result
- [ ] `verifyiq run auto --json` — outputs raw JSON
- [ ] `verifyiq agents` — renders rich table with registered agents
- [ ] `verifyiq agents --json` — outputs raw JSON
- [ ] `verifyiq history` — renders rich table with past requests
- [ ] `verifyiq history --limit 3` — shows at most 3 items
- [ ] `verifyiq inspect <task_id>` — renders rich panels with full artifact dump
- [ ] `verifyiq inspect <task_id> --json` — outputs raw JSON
- [ ] `verifyiq inspect nonexistent-id` — prints "Task not found" with exit code 1

**Use Case Verification:**
- [ ] UC-1a: 4 agents invoked (equifax, employment, intl, synthesis), all completed
- [ ] UC-1b: 4 agents invoked, intl completed (possibly unavailable), decision present
- [ ] UC-2: intl skipped, 3 agents completed
- [ ] UC-3: intl skipped, 3 agents completed, synthesis has decision
- [ ] UC-4: equifax skipped, intl + employment + synthesis completed
- [ ] All `correlation_id` values consistent across full dump for each request

**Test Suite:**
- [ ] `pytest tests/test_phase7_cli.py -v` — all 10 tests pass
- [ ] `pytest tests/ -v` — all Phase 3, 5, 6, and 7 tests pass (no regressions)
- [ ] `git log --oneline | head -2` shows Phase 7 commit

---

*Document: VerifyIQ Implementation Guide | Phase 7 | Companion to Spec v1.7*
