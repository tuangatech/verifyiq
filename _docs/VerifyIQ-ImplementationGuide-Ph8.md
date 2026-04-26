# VerifyIQ — Implementation Guide
## Phase 8 — Auth & Polish
*Companion to Project Specification v1.7*

---

## How to Use This Guide

This guide describes **what to build, why, and how to verify it**. It gives enough context to generate correct implementations without prescribing every line. Focus is on intent, structure, constraints, and verification — not code.

**Conventions:**

- `$` lines are terminal commands you run directly
- Pseudo code blocks show intent and structure, not copy-paste implementations
- ✅ Checklist items at the end must all pass before proceeding

---

## Phase 8 Overview

**Goal:** Production-grade security hygiene and observability. Bearer token validation on all protected endpoints. Structured JSON logging across all services. README for onboarding. Five deterministic subject persona fixtures.

Phase 8 has four parts:
1. **Bearer token auth** — shared secret via env var, validated on protected endpoints
2. **Structured logging** — `structlog` JSON logging across all 6 services (replacing `print()`)
3. **README** — architecture diagram, quick-start, troubleshooting
4. **Subject persona fixtures** — five deterministic test subjects

**Estimated time:** 3–4 hours

**Prerequisite:** Phase 7 complete. `pytest tests/ -v` passes all Phase 3, 5, 6, and 7 tests.

---

## Part A — Bearer Token Auth

### Design Decisions

**Shared secret, not JWT.** All services run on the same Docker network and are owned by the same team. A shared bearer token (plain string compared with `secrets.compare_digest`) is sufficient. JWT adds key management complexity without proportional value for a learning project.

**One env var: `VERIFYIQ_AUTH_TOKEN`.** All services read the same token. The Orchestrator sends it in outbound requests; agents and the Registry validate it on inbound requests. If unset, auth is disabled (backward-compatible for local dev without Docker).

**Which endpoints are protected:**

| Service | Protected endpoints | Unprotected |
|---|---|---|
| Registry | `POST /register`, `DELETE /agents/{url_hash}` | `GET /agents`, `GET /health` |
| Equifax | `POST /tasks/send` | `GET /tasks/{id}`, `GET /.well-known/agent.json`, `GET /health` |
| Employment | `POST /tasks/send` | `GET /tasks/{id}`, `GET /.well-known/agent.json`, `GET /health` |
| Intl | `POST /tasks/send` | `GET /tasks/{id}`, `GET /.well-known/agent.json`, `GET /health` |
| Synthesis | `POST /tasks/send` | `GET /tasks/{id}`, `GET /.well-known/agent.json`, `GET /health` |
| Orchestrator | `POST /verify` | `GET` endpoints, `GET /health`, `GET /.well-known/agent.json` |

**Rationale:** Read endpoints (`GET /agents`, `GET /health`, Agent Cards) remain open — they expose no sensitive data and are needed for discovery and monitoring. Write endpoints that trigger work or mutate state are protected.

---

### Step 8.1 — Auth Utility in `agents/shared/`

**File:** `agents/shared/auth.py` (new)

Create a reusable FastAPI dependency that extracts and validates the bearer token. Every protected endpoint will use this as a `Depends()`.

**Pseudo code:**

```python
# agents/shared/auth.py

import os
import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer(auto_error=False)

def get_auth_token() -> str | None:
    """Read VERIFYIQ_AUTH_TOKEN from env. None means auth is disabled."""
    return os.environ.get("VERIFYIQ_AUTH_TOKEN")

def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """FastAPI dependency — returns the validated token or raises 401.

    If VERIFYIQ_AUTH_TOKEN is not set, auth is disabled (pass-through).
    If set, the request must include a matching Authorization: Bearer <token>.
    Uses secrets.compare_digest to prevent timing attacks.
    """
    expected = get_auth_token()
    if expected is None:
        # Auth disabled — allow request
        return "no-auth"

    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Authorization header")

    if not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    return credentials.credentials
```

**Key points:**
- `auto_error=False` on `HTTPBearer` so we control the error response (not FastAPI's default)
- `secrets.compare_digest` prevents timing-based token guessing
- When `VERIFYIQ_AUTH_TOKEN` is unset, all requests pass through — existing tests and local dev without tokens still work
- This file is copied into every container at build time (it's in `agents/shared/`)

---

### Step 8.2 — Apply Auth to Data Agents

**Files:** `agents/equifax/main.py`, `agents/employment/main.py`, `agents/intl/main.py`, `agents/synthesis/main.py`

Add `require_auth` as a dependency on `POST /tasks/send` only.

**Pseudo code (same pattern for all four agents):**

```python
from agents.shared.auth import require_auth

@app.post("/tasks/send")
async def tasks_send(task: A2ATask, _token: str = Depends(require_auth)) -> A2ATaskResult:
    # existing implementation unchanged
    ...
```

The `_token` parameter is unused in the function body — it's only there to trigger the dependency. The underscore prefix signals this is intentional.

**No changes to:** `GET /tasks/{task_id}`, `GET /.well-known/agent.json`, `GET /health` — these remain unprotected.

---

### Step 8.3 — Apply Auth to Registry

**File:** `agents/registry/main.py`

Protect `POST /register` and `DELETE /agents/{url_hash}`.

```python
from agents.shared.auth import require_auth

@app.post("/register", status_code=201)
def register(body: RegisterRequest, _token: str = Depends(require_auth)):
    # existing implementation unchanged
    ...

@app.delete("/agents/{url_hash}")
def deregister(url_hash: str, _token: str = Depends(require_auth)):
    # existing implementation unchanged
    ...
```

**No changes to:** `GET /agents`, `GET /health`.

---

### Step 8.4 — Apply Auth to Orchestrator `POST /verify`

**File:** `agents/orchestrator/__init__.py`

Protect the user-facing `POST /verify` endpoint.

```python
from agents.shared.auth import require_auth

@app.post("/verify")
async def verify(body: VerificationRequest, _token: str = Depends(require_auth)) -> VerifyResponse:
    # existing implementation unchanged
    ...
```

**No changes to:** `GET` endpoints — all read-only routes remain open.

---

### Step 8.5 — Orchestrator Sends Token in Outbound Requests

The Orchestrator must include the bearer token when it dispatches tasks to agents and when agents register with the Registry.

**File:** `agents/orchestrator/dispatcher.py`

The `TaskDispatcher._send()` method needs to include the `Authorization` header.

**Pseudo code:**

```python
class TaskDispatcher:
    def __init__(self, timeout: float = 35.0):
        self.timeout = timeout
        self.auth_token = os.environ.get("VERIFYIQ_AUTH_TOKEN")

    def _auth_headers(self) -> dict[str, str]:
        """Return Authorization header if token is configured."""
        if self.auth_token:
            return {"Authorization": f"Bearer {self.auth_token}"}
        return {}

    async def _send(self, agent_url: str, task: A2ATask) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{agent_url}/tasks/send",
                json=task.model_dump(),
                headers=self._auth_headers(),
            )
            response.raise_for_status()
            return response
```

**File:** `agents/shared/registry_client.py`

The `register_with_registry()` and `deregister_from_registry()` functions need to include the auth header when calling the Registry.

**Pseudo code:**

```python
def _auth_headers() -> dict[str, str]:
    token = os.environ.get("VERIFYIQ_AUTH_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}

async def register_with_registry(...) -> str | None:
    ...
    response = await client.post(
        f"{registry_url}/register",
        json=payload,
        headers=_auth_headers(),
    )
    ...

async def deregister_from_registry(url_hash: str | None) -> None:
    ...
    response = await client.delete(
        f"{registry_url}/agents/{url_hash}",
        headers=_auth_headers(),
    )
    ...
```

---

### Step 8.6 — CLI Sends Token

**File:** `cli/verifyiq_cli/client.py`

The CLI reads `VERIFYIQ_AUTH_TOKEN` from the environment and includes it in requests to `POST /verify`.

**Pseudo code:**

```python
class VerifyIQClient:
    def __init__(self):
        self.base_url = os.environ.get("VERIFYIQ_URL", "http://localhost:8000")
        self.auth_token = os.environ.get("VERIFYIQ_AUTH_TOKEN")

    def _auth_headers(self) -> dict[str, str]:
        if self.auth_token:
            return {"Authorization": f"Bearer {self.auth_token}"}
        return {}

    def submit_verification(self, payload: dict) -> dict:
        response = self.client.post(
            f"{self.base_url}/verify",
            json=payload,
            headers=self._auth_headers(),
        )
        ...
```

Only `submit_verification` needs the token — all `GET` endpoints are unprotected.

---

### Step 8.7 — Environment Variable Setup

**File:** `.env.example` — add:

```
VERIFYIQ_AUTH_TOKEN=verifyiq-dev-token-2026
```

**File:** `docker-compose.yml` — pass the token to all services:

```yaml
# Add to every service's environment block:
- VERIFYIQ_AUTH_TOKEN=${VERIFYIQ_AUTH_TOKEN}
```

All 6 services (registry, orchestrator, equifax, employment, intl, synthesis) need this env var.

**Local dev without Docker:** If running an agent outside Docker, set `VERIFYIQ_AUTH_TOKEN` in the shell or leave it unset (auth disabled).

---

### Step 8.8 — Smoke Test Auth

```bash
$ docker compose down && docker compose up --build -d
$ sleep 10

# Verify auth is enforced — no token should get 401
$ curl -s -X POST http://localhost:8000/verify \
    -H "Content-Type: application/json" \
    -d '{"subject_name":"Test","subject_id":"S-1","use_case":"rental","has_foreign_addr":false,"consent":true}'
# Expect: 401 Unauthorized

# Verify auth with valid token works
$ curl -s -X POST http://localhost:8000/verify \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer verifyiq-dev-token-2026" \
    -d '{"subject_name":"Test","subject_id":"S-1","use_case":"rental","has_foreign_addr":false,"consent":true}'
# Expect: 200 with task_id

# Verify GET endpoints still work without token
$ curl -s http://localhost:8000/agents
# Expect: 200 with agent list

# Verify the full pipeline works (agents call each other with token)
$ verifyiq run rental
# Expect: Pipeline completes successfully (CLI reads token from env)

# Verify direct agent auth
$ curl -s -X POST http://localhost:8001/tasks/send \
    -H "Content-Type: application/json" \
    -d '{"task_id":"t1","correlation_id":"c1","skill":"credit_score","input":{}}'
# Expect: 401 Unauthorized
```

---

## Part B — Structured Logging

### Design Decisions

**`structlog` with JSON output.** The Orchestrator already uses `structlog`. Extend this to all services. JSON output makes logs greppable and parseable by `docker compose logs | jq`.

**Shared configuration.** Add a `configure_logging()` function to `agents/shared/` that all services call on startup. Consistent format across all containers.

**Bound context fields.** Every log line includes: `agent` (service name), `timestamp`. Task-related logs add: `task_id`, `correlation_id`, `skill`, `duration_ms`.

---

### Step 8.9 — Shared Logging Configuration

**File:** `agents/shared/logging.py` (new)

**Pseudo code:**

```python
# agents/shared/logging.py

import structlog

def configure_logging(agent_name: str) -> None:
    """Configure structlog with JSON output and bound agent name.

    Call once at service startup (in lifespan or before app creation).
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    # Bind agent name globally so every log line includes it
    structlog.contextvars.bind_contextvars(agent=agent_name)
```

---

### Step 8.10 — Replace `print()` with `structlog` in All Services

**Services to update:** Registry, Equifax, Employment, Intl, Synthesis.

The Orchestrator already uses `structlog` — verify its configuration matches the shared pattern and update if needed.

**Pattern for each service:**

1. Import `configure_logging` and call it in the `lifespan` (or before `app` creation)
2. Replace `print(f"[Registry Client] ...")` with `logger.info("event_name", key=value)`
3. Replace `print(f"[Agent] ...")` in tools/prompts with structured log calls

**Registry example (pseudo):**

```python
# agents/registry/main.py
from agents.shared.logging import configure_logging

configure_logging("registry")
logger = structlog.get_logger()

# Before:
print(f"[Registry] Registered: {body.name} @ {body.url} | skills: {skills_str}")

# After:
logger.info("agent_registered", name=body.name, url=body.url, skills=body.skills)
```

**Data agent example (pseudo):**

```python
# agents/equifax/main.py
from agents.shared.logging import configure_logging

configure_logging("equifax")
logger = structlog.get_logger()

@app.post("/tasks/send")
async def tasks_send(task: A2ATask, ...) -> A2ATaskResult:
    log = logger.bind(task_id=task.task_id, correlation_id=task.correlation_id, skill=task.skill)
    log.info("task_received")
    ...
    log.info("task_completed", duration_ms=..., status=result.status)
    return result
```

**`registry_client.py`:** Replace `print()` statements with `structlog` calls. Since this module is used by all agents, it picks up whatever logger configuration the importing service has set.

**Key events to log (per service):**

| Service | Events |
|---|---|
| Registry | `agent_registered`, `agent_deregistered`, `agents_queried` |
| Equifax | `task_received`, `llm_called`, `llm_retry`, `task_completed`, `task_failed` |
| Employment | `task_received`, `graph_started`, `node_completed`, `task_completed`, `task_failed` |
| Intl | `task_received`, `foreign_addr_check`, `llm_called`, `task_completed`, `task_failed` |
| Synthesis | `task_received`, `outcomes_parsed`, `llm_called`, `task_completed`, `task_failed` |
| Registry Client | `registration_success`, `registration_failed`, `deregistration_success` |

---

### Step 8.11 — Add `structlog` to All `requirements.txt`

**Files to update:**
- `agents/registry/requirements.txt` — add `structlog==25.4.0`
- `agents/equifax/requirements.txt` — add `structlog==25.4.0`
- `agents/employment/requirements.txt` — add `structlog==25.4.0`
- `agents/intl/requirements.txt` — add `structlog==25.4.0`
- `agents/synthesis/requirements.txt` — add `structlog==25.4.0`

The Orchestrator already has it.

---

### Step 8.12 — Verify Logging

```bash
$ docker compose down && docker compose up --build -d
$ sleep 10

# Submit a request
$ verifyiq run mortgage-intl

# Check logs are structured JSON
$ docker compose logs orchestrator --tail 20
# Expect: JSON lines with "agent", "event", "correlation_id", "task_id"

$ docker compose logs equifax --tail 10
# Expect: JSON lines with "agent": "equifax", "event": "task_received", etc.

$ docker compose logs registry --tail 10
# Expect: JSON lines with "agent": "registry", "event": "agent_registered", etc.

# Verify correlation_id tracing works
$ docker compose logs | grep "CORRELATION_ID_FROM_OUTPUT"
# Expect: Lines from orchestrator + equifax + employment + intl + synthesis
```

---

## Part C — README

### Step 8.13 — Write `README.md`

**File:** `README.md` (repo root)

**Sections:**

1. **Title + one-line description:** "VerifyIQ — Multi-Agent Credit & Employment Verification Platform"
2. **What is this?** — 2-3 sentences: learning project for A2A orchestration, simulates credit bureaus, multiple independent agents collaborating via protocol
3. **Architecture diagram** — the ASCII diagram from the spec (Section 5), or a simplified version
4. **Quick Start:**
   - Prerequisites: Docker Desktop, Python 3.11+, `uv`, OpenRouter API key
   - Clone, copy `.env.example` to `.env`, fill in `OPENROUTER_API_KEY`
   - `docker compose up --build -d`
   - `uv venv && source .venv/Scripts/activate && uv pip install -e cli/`
   - `verifyiq run mortgage-intl`
5. **CLI Commands** — table of commands with one-line descriptions
6. **Use Cases** — the UC table from CLAUDE.md
7. **Port Map** — table of services and ports
8. **Running Tests** — `pytest tests/ -v`
9. **Environment Variables** — table from `.env.example`
10. **Project Structure** — brief directory layout
11. **Tech Stack** — layer table from the spec

Keep it concise — this is a side project README, not enterprise docs. Someone cloning the repo should be running a demo in under 5 minutes.

---

## Part D — Subject Persona Fixtures

### Step 8.14 — Deterministic Test Fixtures

**File:** `tests/fixtures.py` (new)

**Purpose:** Five pre-defined subject personas that produce consistent, predictable results across test runs. These are the same subjects used in `cli/verifyiq_cli/scenarios.py` but packaged as pytest-ready dicts.

**Fixtures:**

| Fixture name | Subject | Use case | Expected agents | Expected pattern |
|---|---|---|---|---|
| `mortgage_intl_subject` | Nguyen Minh Tuan, SIM-VN-2019 | `mortgage` | All 4 | UC-1a: intl data available |
| `mortgage_domestic_subject` | Sarah Johnson, SIM-US-1985 | `mortgage` | All 4 | UC-1b: intl dispatched |
| `rental_subject` | Marcus Williams, SIM-US-1992 | `rental` | 3 (no intl) | UC-2: intl skipped |
| `auto_subject` | Jennifer Chen, SIM-US-1988 | `auto` | 3 (no intl) | UC-3: DTI-weighted |
| `hire_subject` | Raj Patel, SIM-IN-2020 | `hire` | 3 (no equifax) | UC-4: equifax skipped |

**Implementation:** Each fixture is a `@pytest.fixture` returning a dict matching the `VerificationRequest` schema. Import these in test files to replace inline payload dicts.

**Pseudo code:**

```python
# tests/fixtures.py

import pytest

@pytest.fixture
def mortgage_intl_subject() -> dict:
    return {
        "subject_name": "Nguyen Minh Tuan",
        "subject_id": "SIM-VN-2019",
        "use_case": "mortgage",
        "has_foreign_addr": True,
        "consent": True,
    }

# ... same pattern for the other four
```

**Update existing tests** to import from `fixtures.py` where they currently define inline payloads. This is optional cleanup — existing tests continue to work either way.

---

## Phase 8 Tests

**File:** `tests/test_phase8_auth.py`

---

### Auth Tests (8 tests)

**1. `test_verify_rejects_missing_token`**

`POST /verify` with no `Authorization` header. Expect 401.

**2. `test_verify_rejects_invalid_token`**

`POST /verify` with `Authorization: Bearer wrong-token`. Expect 401.

**3. `test_verify_accepts_valid_token`**

`POST /verify` with valid token. Expect 200 with `task_id`.

**4. `test_agent_tasks_send_rejects_missing_token`**

`POST /tasks/send` directly to Equifax agent (port 8001) with no token. Expect 401.

**5. `test_agent_tasks_send_accepts_valid_token`**

`POST /tasks/send` directly to Equifax with valid token. Expect 200.

**6. `test_registry_register_rejects_missing_token`**

`POST /register` to Registry (port 8099) with no token. Expect 401.

**7. `test_get_endpoints_require_no_auth`**

`GET /agents`, `GET /health`, `GET /.well-known/agent.json` on Orchestrator — all return 200 with no token.

**8. `test_full_pipeline_works_with_auth`**

Submit a verification via `POST /verify` with valid token. Wait for completion. Verify the pipeline succeeds — agents authenticated each other internally.

---

### Test Configuration Note

Tests need the `VERIFYIQ_AUTH_TOKEN` env var set to match what Docker Compose uses. Add it to the test environment or read from `.env`.

**Pseudo code for test helper:**

```python
AUTH_TOKEN = os.environ.get("VERIFYIQ_AUTH_TOKEN", "verifyiq-dev-token-2026")
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}

# Use in tests:
response = await client.post("/verify", json=payload, headers=AUTH_HEADERS)
```

**Update `conftest.py`:** The existing `_submit_and_wait` and `_verify_request` helpers need to include the auth header. Add the `Authorization` header to all `POST` requests in these helpers.

> This is critical — **all existing Phase 3, 5, 6, and 7 tests will break** if they don't send the token. Update the helpers first, then run the full test suite to verify nothing regresses.

---

## Running Tests

```bash
$ docker compose down && docker compose up --build -d
$ sleep 10

# Set token for CLI and tests
$ export VERIFYIQ_AUTH_TOKEN=verifyiq-dev-token-2026

# Phase 8 only
$ pytest tests/test_phase8_auth.py -v

# Full regression (critical — existing tests must still pass with auth)
$ pytest tests/ -v
```

**Common failure modes:**

| Symptom | Likely Cause |
|---|---|
| All existing tests return 401 | Test helpers not sending auth header; update `conftest.py` |
| Agents fail to register on startup | `registry_client.py` not sending auth header; or `VERIFYIQ_AUTH_TOKEN` not in docker-compose env |
| Pipeline completes on Orchestrator but agents return 401 | `dispatcher.py` not including auth header in outbound requests |
| Tests pass locally but fail in Docker | Token mismatch between `.env` and test env var |
| `structlog` import errors | Missing from `requirements.txt` in some agents |
| Logs not JSON | `configure_logging()` not called before first log statement |

---

## Commit

```bash
$ git add .
$ git commit -m "Phase 8: Bearer token auth, structlog logging, README, test fixtures"
```

---

## ✅ Phase 8 Verification Checklist

**Auth — Token Enforcement:**
- [ ] `POST /verify` without token returns 401
- [ ] `POST /verify` with wrong token returns 401
- [ ] `POST /verify` with valid token returns 200
- [ ] `POST /tasks/send` on any data agent without token returns 401
- [ ] `POST /register` on Registry without token returns 401
- [ ] `DELETE /agents/{hash}` on Registry without token returns 401
- [ ] `GET /agents`, `GET /health`, `GET /.well-known/agent.json` work without token

**Auth — Pipeline Integration:**
- [ ] `verifyiq run rental` completes successfully with `VERIFYIQ_AUTH_TOKEN` set
- [ ] Agents register successfully on startup (auth header included in registration)
- [ ] Orchestrator dispatches tasks successfully (auth header included in dispatch)
- [ ] CLI sends token from env var on `POST /verify`

**Auth — Backward Compatibility:**
- [ ] With `VERIFYIQ_AUTH_TOKEN` unset, all endpoints accept requests (auth disabled)
- [ ] Existing tests pass after updating helpers with auth headers

**Logging:**
- [ ] All 6 services output structured JSON logs
- [ ] No remaining `print()` statements in production code (except CLI rich output)
- [ ] `docker compose logs orchestrator` shows JSON with `agent`, `event`, `correlation_id`
- [ ] `docker compose logs equifax` shows JSON with `task_received`, `task_completed`
- [ ] `docker compose logs registry` shows JSON with `agent_registered`
- [ ] `docker compose logs | grep <correlation_id>` returns lines from multiple services

**README:**
- [ ] `README.md` exists at repo root
- [ ] Quick-start section gets from clone to running demo in under 5 minutes
- [ ] Architecture diagram present
- [ ] CLI commands documented
- [ ] Port map and env vars documented

**Test Fixtures:**
- [ ] `tests/fixtures.py` has 5 persona fixtures
- [ ] At least one test uses the fixtures

**Test Suite:**
- [ ] `pytest tests/test_phase8_auth.py -v` — all 8 tests pass
- [ ] `pytest tests/ -v` — all Phase 3, 5, 6, 7, and 8 tests pass (no regressions)

---

*Document: VerifyIQ Implementation Guide | Phase 8 | Companion to Spec v1.7*
