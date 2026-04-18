# VerifyIQ — Implementation Guide
## Phases 1 and 2
*Companion to Project Specification v1.7*

---

## How to Use This Guide

This guide describes **what to build and how to verify it** — not the full code. Each step tells you what a file should do, what shape it should take, and what behaviour to expect. Use this as the prompt context for Claude Code to generate the actual file contents.

**Conventions:**

- `$` lines are terminal commands you run directly
- **File description blocks** tell Claude Code what to generate — they describe purpose, structure, key logic, and expected behaviour without containing complete source
- ✅ Checklist items at the end of each phase must all pass before proceeding

---

## Phase 1 — Setup, Skeletons & Agent Cards

**Goal:** All tools verified. Repository initialised. All 6 containers start and respond. Agent Cards are readable — you understand every agent's contract before writing any protocol code.

**Estimated time:** 3–5 hours

---

### Step 1.1 — Install Docker Desktop

Download and install from [https://docker.com/products/docker-desktop](https://docker.com/products/docker-desktop).

After installation, start Docker Desktop and wait for the whale icon to show "running". Then verify:

```bash
$ docker --version
$ docker compose version
```

Both must print a version number. If `docker compose version` fails, you have an older install — upgrade to Docker Desktop v4+.

---

### Step 1.2 — Install Python 3.11+

```bash
$ python3 --version         # expect 3.11.x or 3.12.x
```

If you need to install: `brew install python@3.11` on macOS, or use [pyenv](https://github.com/pyenv/pyenv).

---

### Step 1.3 — Install `uv`

```bash
$ pip install uv
$ uv --version
```

---

### Step 1.4 — Install SQLite CLI

```bash
$ sqlite3 --version         # usually pre-installed on macOS/Linux
```

If missing: `brew install sqlite` (macOS) or `sudo apt-get install sqlite3` (Linux).

---

### Step 1.5 — Create OpenRouter Account and Verify API Key

1. Sign up at [https://openrouter.ai](https://openrouter.ai)
2. Navigate to **Keys → Create Key** → name it `verifyiq-dev`
3. Copy the key (starts with `sk-or-v1-...`)
4. Navigate to **Credits** → add $5 USD
5. Test the key:

```bash
$ curl https://openrouter.ai/api/v1/models \
    -H "Authorization: Bearer sk-or-v1-YOUR_KEY" \
    | python3 -m json.tool | head -20
```

Expected: a JSON array of model objects. If you see `{"error": ...}` the key is wrong or has no credit.

---

### Step 1.6 — Initialise the Repository

```bash
$ mkdir verifyiq && cd verifyiq
$ git init && git branch -m main
```

Create the full folder structure:

```bash
$ mkdir -p agents/shared
$ mkdir -p agents/registry
$ mkdir -p agents/orchestrator
$ mkdir -p agents/equifax
$ mkdir -p agents/employment
$ mkdir -p agents/intl
$ mkdir -p agents/synthesis
$ mkdir -p tests
```

Verify:

```bash
$ find agents -type d | sort
```

Expected output:
```
agents
agents/employment
agents/equifax
agents/intl
agents/orchestrator
agents/registry
agents/shared
agents/synthesis
```

---

### Step 1.7 — Create `.gitignore`

**File:** `.gitignore`

**Purpose:** Prevent secrets, compiled files, and generated artefacts from being committed.

**Must exclude:** `.env`, `*.db`, `__pycache__/`, `*.pyc`, `.venv/`, `dist/`

---

### Step 1.8 — Create `.env.example` and `.env`

**File:** `.env.example`

**Purpose:** Documents every environment variable the project needs. Committed to git. Contains placeholder values only — never real secrets.

**Variables to include:**

| Variable | Default / placeholder | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | `sk-or-v1-your-key-here` | Single key for all LLM calls |
| `OPENROUTER_AGENT_MODEL` | `google/gemini-3-flash` | Model for data agents |
| `OPENROUTER_SYNTHESIS_MODEL` | `openai/gpt-5.4-mini` | Model for Risk Synthesis Agent |
| `AGENT_REGISTRY_URL` | `http://registry:8099` | Registry URL (Docker service name for Compose; `http://localhost:8099` for local dev) |
| `AGENT_TIMEOUT_SECONDS` | `30` | Global A2A task timeout |

After creating `.env.example`:

```bash
$ cp .env.example .env
```

Open `.env` and replace the API key placeholder with your real `sk-or-v1-...` key. Confirm `.env` is listed in `.gitignore` before committing anything.

---

### Step 1.9 — Create Shared Pydantic Schemas

**File:** `agents/shared/__init__.py`

**Purpose:** Empty — marks the directory as a Python package so agents can import from it.

---

**File:** `agents/shared/schemas.py`

**Purpose:** All Pydantic artifact models used across the system. This is the shared contract — every agent that produces or consumes an artifact references these models.

**Models to define:**

`EquifaxArtifact` — output of the Equifax Agent. Fields: `source` (literal `"equifax"`), `subject_id`, `credit_score` (int, 300–850), `score_model` (str, default `"FICO 8"`), `open_accounts` (int ≥ 0), `credit_utilization_pct` (float 0–100), `derogatory_marks` (int ≥ 0), `hard_inquiries_12mo` (int ≥ 0), `oldest_account_years` (float ≥ 0), `data_as_of` (ISO date string).

`EmploymentArtifact` — output of the Employment Agent. Fields: `source` (literal `"workforce_solutions"`), `subject_id`, `currently_employed` (bool), `employer_name` (optional str), `employment_start_date` (optional ISO date), `tenure_years` (optional float), `employment_type` (optional literal: `full_time | part_time | contract | self_employed`), `verified_annual_income` (optional int), `income_currency` (str, default `"USD"`), `verification_confidence` (literal: `high | medium | low`), `data_as_of`.

`InternationalArtifact` — output of the International Agent. Fields: `source` (literal `"experian_international"`), `subject_id`, `data_availability` (literal: `full | partial | unavailable`), `reason` (optional str — populated when unavailable), `country_of_record` (optional ISO 3166-1 alpha-2 string), `local_credit_score` (optional int), `us_equivalent_score` (optional int), `foreign_tradelines` (optional int), `country_risk_tier` (optional literal: `low | moderate | high | very_high`), `data_as_of` (optional str).

`VerificationDecision` — output of the Risk Synthesis Agent. Fields: `decision` (literal: `approve | review | decline`), `confidence` (literal: `high | medium | low`), `risk_score` (int 0–100), `decision_factors` (list of str), `risk_flags` (list of str), `international_note` (optional str), `recommended_actions` (list of str, default empty), `reasoning_summary` (str).

---

**File:** `agents/shared/a2a_types.py`

**Purpose:** All A2A protocol types used by agents and the Orchestrator for inter-agent communication.

**Types to define** (exact field names and types matter — these flow across service boundaries):

`A2ATask` — sent by Orchestrator to a remote agent via `POST /tasks/send`. Fields: `task_id` (str), `correlation_id` (str — same value for all tasks in one verification request), `skill` (str), `input` (dict), `timeout_ms` (int, default 30000), `attempt` (int, default 1).

`A2ATaskResult` — returned by a remote agent. Fields: `task_id` (str), `correlation_id` (str — must echo back unchanged from the task), `status` (literal: `completed | failed | timed_out`), `artifact` (optional dict), `error` (optional `AgentError`), `started_at` (ISO datetime str), `ended_at` (ISO datetime str).

`AgentError` — structured error on failed or timed-out tasks. Fields: `code` (str — e.g. `"LLM_VALIDATION_FAILED"`, `"TIMEOUT"`, `"UPSTREAM_ERROR"`), `message` (str), `retryable` (bool — `True` for 5xx and timeout; `False` for schema/logic errors).

`AgentOutcome` — what the Orchestrator packages per data agent before passing to Risk Synthesis. Fields: `agent_name` (str), `skill` (str), `status` (literal: `completed | failed | timed_out | skipped`), `artifact` (optional dict), `error` (optional `AgentError`).

`RegisterRequest` — payload sent by each agent to `POST /register` on the Registry. Fields: `name` (str), `url` (str), `skills` (list of str), `version` (str, default `"1.0.0"`), `auth_schemes` (list of str, default empty), `capabilities` (dict, default empty), `agent_card_raw` (dict — the full Agent Card JSON).

`RegisteredAgent` — returned by `GET /agents` on the Registry. Fields: `url_hash` (str), `name` (str), `url` (str), `skills` (list of str), `version` (str), `health` (literal: `healthy | unhealthy | unknown`), `avg_latency_ms` (optional int), `registered_at` (ISO datetime str), `last_seen` (optional ISO datetime str).

---

**File:** `agents/shared/registry_client.py`

**Purpose:** Async helper functions used by every agent's lifespan hook to self-register and deregister with the Registry. Kept in shared so no agent duplicates this logic.

**Functions to implement:**

`register_with_registry(agent_card_path: str = "agent_card.json") -> str | None`
- Reads the Agent Card JSON from the given path
- Builds a `RegisterRequest` payload (name, url, skills extracted from card's `skills[].id`, version, auth_schemes, capabilities, agent_card_raw)
- POSTs to `{AGENT_REGISTRY_URL}/register` using `httpx.AsyncClient` with a 5-second timeout
- On success: logs the returned `url_hash` and returns it
- On any exception (connection refused, timeout, non-2xx): logs a warning and returns `None` — **must not raise**, the agent should still start even if the Registry is briefly unavailable

`deregister_from_registry(url_hash: str | None) -> None`
- If `url_hash` is None or `AGENT_REGISTRY_URL` is unset, returns immediately (no-op)
- Sends `DELETE {AGENT_REGISTRY_URL}/agents/{url_hash}` with a 5-second timeout
- On any exception: logs a warning and returns — **must not raise**

Both functions read `AGENT_REGISTRY_URL` from the environment at call time, not at import time.

---

### Step 1.10 — Create Agent Card JSON Files

Six static JSON files. Each is the formal contract advertised by that agent. The `url` field uses the Docker Compose service name (e.g. `http://equifax:8001`) — this resolves correctly inside the Docker network.

**File:** `agents/equifax/agent_card.json`

```
name: "Equifax Credit Agent"
description: Simulates Equifax U.S. Information Solutions. Provides domestic credit scores,
             tradeline summaries, derogatory mark history, and hard inquiry records.
url: http://equifax:8001
version: 1.0.0
skills: [credit_score, tradeline_summary, derogatory_marks, inquiry_history]
  — each skill has id, name, description, input_modes: ["application/json"],
    output_modes: ["application/json"]
auth_schemes: []
capabilities: { streaming: false, push_notifications: false, max_concurrent_tasks: 10 }
```

**File:** `agents/employment/agent_card.json`

```
name: "Employment Verification Agent"
description: Simulates Equifax Workforce Solutions / The Work Number. Verifies employment
             status, tenure, and income from payroll records.
url: http://employment:8002
skills: [employment_status, income_verification, employment_history]
```

**File:** `agents/intl/agent_card.json`

```
name: "Experian International Agent"
description: Simulates Experian International credit bureau. Returns foreign credit scores
             and country risk profiles. Returns unavailable when no foreign address history
             exists for the subject.
url: http://intl:8003
skills: [international_credit_score, country_risk_profile, foreign_tradelines]
```

**File:** `agents/synthesis/agent_card.json`

```
name: "Risk Synthesis Agent"
description: Receives structured outcome bundles from data agents and produces a risk
             decision (approve / review / decline) with decision factors and reasoning.
url: http://synthesis:8004
skills: [risk_synthesis]
capabilities: { max_concurrent_tasks: 5 }
```

**File:** `agents/orchestrator/agent_card.json`

```
name: "VerifyIQ Orchestrator"
description: Hub agent. Accepts verification requests, resolves agents via Registry,
             fans out to data agents, chains to Risk Synthesis, streams progress via SSE.
             Also acts as an A2A server for external orchestrators (e.g. Mortgage Platform).
url: http://orchestrator:8000
skills: [verify_subject]
  — verify_subject output_modes: ["application/json", "text/event-stream"]
capabilities: { streaming: true, push_notifications: false, max_concurrent_tasks: 20 }
```

The Registry has no Agent Card — it is plain infrastructure, not an A2A agent.

---

### Step 1.11 — Create `requirements.txt` for Each Agent

Each agent has its own `requirements.txt`. Dependencies are repeated across agents intentionally — each service is independently deployable.

**`agents/registry/requirements.txt`**
Needs: `fastapi`, `uvicorn[standard]`, `pydantic`, `aiosqlite`

**`agents/orchestrator/requirements.txt`**
Needs: `fastapi`, `uvicorn[standard]`, `pydantic`, `httpx`, `aiosqlite`, `python-dotenv`
*(ADK will be added in Phase 9 — keep the orchestrator's dependencies minimal until then)*

**`agents/equifax/requirements.txt`**
Needs: `fastapi`, `uvicorn[standard]`, `pydantic`, `httpx`, `python-dotenv`

**`agents/employment/requirements.txt`**
Needs: `fastapi`, `uvicorn[standard]`, `pydantic`, `httpx`, `langgraph`, `python-dotenv`

**`agents/intl/requirements.txt`**
Needs: `fastapi`, `uvicorn[standard]`, `pydantic`, `httpx`, `python-dotenv`

**`agents/synthesis/requirements.txt`**
Needs: `fastapi`, `uvicorn[standard]`, `pydantic`, `httpx`, `python-dotenv`

Pin to specific versions for reproducibility. Suggested baseline: `fastapi==0.115.0`, `uvicorn[standard]==0.30.6`, `pydantic==2.8.2`, `httpx==0.27.2`, `aiosqlite==0.20.0`, `langgraph==0.2.28`, `python-dotenv==1.0.1`.

---

### Step 1.12 — Create Dockerfiles

**Pattern for all Python agents** (`agents/{name}/Dockerfile`):

```
Base image: python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
Working directory: /app
Copy agents/shared/ to /app/agents/shared/    ← shared Pydantic types available to all agents
Copy agents/{name}/requirements.txt to /app/
RUN pip install --no-cache-dir -r requirements.txt
Copy agents/{name}/ to /app/
EXPOSE {port}
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "{port}"]
```

> ⚠️ **Note for orchestrator only** — because `resolver.py` is a sub-module, orchestrator uses a package structure instead of a flat `main.py`:
> - `agents/orchestrator/__init__.py` — contains the `FastAPI` app (`app`) and all routes
> - `agents/orchestrator/__main__.py` — thin entrypoint: `import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8000)`
> - `agents/orchestrator/resolver.py` — the `AgentResolver` class
> - `COPY agents/orchestrator/ /app/orchestrator/` (not `/app/`)
> - `CMD ["python", "-m", "orchestrator"]`
>
> The orchestrator also needs `agents/orchestrator/__init__.py` (can be empty — marks the directory as a Python package). Without it, Python cannot resolve `from .resolver import ...`.

Ports: registry=8099, orchestrator=8000, equifax=8001, employment=8002, intl=8003, synthesis=8004.

The `COPY agents/shared` step must come before the `pip install` step — shared types are needed at runtime, not at build time.

> ⚠️ **Note:** The `curl` installation is required for Docker healthchecks (`curl -f http://localhost:{port}/health`). The `python:slim-bookworm` image does not include `curl` by default.

---

### Step 1.13 — Create Skeleton `main.py` for Each Agent

All six agents get a skeleton `main.py` in Phase 1. They serve only two endpoints: `GET /.well-known/agent.json` and `GET /health`. No lifespan hooks yet (those come in Phase 2). No database, no LLM, no task protocol.

**Pattern for each data agent** (equifax, employment, intl, synthesis):

```
- Import FastAPI, json, Path
- Load agent card at module level: read agent_card.json as a dict
- GET /.well-known/agent.json → return the loaded dict
- GET /health → return { "status": "healthy", "agent": "<name>", "port": <port> }
```

**`agents/registry/main.py`** — Phase 1 stub only:

```
- GET /health → { "status": "healthy", "service": "registry", "port": 8099 }
- GET /agents → return [] (empty list — no persistence yet, full implementation in Phase 2)
```

**`agents/orchestrator/`** — Phase 1 stub uses the package structure (see Step 1.13 note):

```
- agents/orchestrator/__init__.py — FastAPI app + routes (same as main.py above)
- agents/orchestrator/__main__.py — uvicorn entrypoint
- GET /.well-known/agent.json → return card
- GET /health → { "status": "healthy", "agent": "orchestrator", "port": 8000 }
- GET /agents → return [] (stub — will proxy Registry in Phase 2)
```

The `sys.path` must include `/app` so agents can import from `agents.shared`. Add `sys.path.insert(0, "/app")` at the top of each `__init__.py`.

---

### Step 1.14 — Create `docker-compose.yml`

**File:** `docker-compose.yml` at repo root.

**What it must define:**

Six services: `registry`, `orchestrator`, `equifax`, `employment`, `intl`, `synthesis`. (The `mortgage-platform` service is added in Phase 9.)

**For each service:**
- `build.context: .` and `build.dockerfile: agents/{name}/Dockerfile`
- `ports`: host port maps to container port (same number for all — e.g. `"8001:8001"` for equifax)
- `environment`: inject env vars from `.env` — each service needs the vars it uses
- `depends_on`: data agents and orchestrator depend on `registry` with `condition: service_healthy`
- `healthcheck`: `test: ["CMD", "curl", "-f", "http://localhost:{port}/health"]`, `interval: 10s`, `timeout: 5s`, `retries: 3`

**Environment variable assignment per service:**

| Service | Env vars needed |
|---|---|
| `registry` | `DB_PATH=/data/registry.db` |
| `orchestrator` | `DB_PATH=/data/verifyiq.db`, `AGENT_REGISTRY_URL`, `OPENROUTER_API_KEY`, `OPENROUTER_SYNTHESIS_MODEL`, `AGENT_TIMEOUT_SECONDS` |
| `equifax` | `AGENT_REGISTRY_URL`, `OPENROUTER_API_KEY`, `OPENROUTER_AGENT_MODEL` |
| `employment` | same as equifax |
| `intl` | same as equifax |
| `synthesis` | `AGENT_REGISTRY_URL`, `OPENROUTER_API_KEY`, `OPENROUTER_SYNTHESIS_MODEL` |

**Volumes:**
- `sqlite_data` mounted to `/data` in the `orchestrator` container
- `registry_data` mounted to `/data` in the `registry` container

These two volumes must be declared at the bottom of the file under `volumes:`.

---

### Step 1.15 — Build and Start

```bash
$ docker compose up --build
```

First build takes 3–5 minutes while Docker pulls base images and installs dependencies. Leave this terminal running. Open a second terminal for verification.

---

### ✅ Phase 1 Verification Checklist

Run every item. All must pass before starting Phase 2.

**Tools:**
- [ ] `docker --version` prints a version
- [ ] `docker compose version` prints a version
- [ ] `python --version` prints `3.11.x` or `3.12.x`
- [ ] `uv --version` prints a version
- [ ] `sqlite3 --version` prints a version

**OpenRouter:**
- [ ] `curl https://openrouter.ai/api/v1/models -H "Authorization: Bearer $(grep OPENROUTER_API_KEY .env | cut -d= -f2)"` returns a JSON array (not an error)
  *(Windows: use `findstr` or extract the key manually — the `$(...)` subshell syntax requires Git Bash or WSL)*
- [ ] OpenRouter dashboard shows credit balance > $0

**Repository:**
- [ ] `cat .gitignore | grep "^\.env"` prints `.env`
- [ ] `cat .gitignore | grep "^\*\.db"` prints `*.db`
- [ ] `.env` exists and contains your real API key (not the placeholder)
- [ ] `.env.example` is committed: `git log --oneline | head -3` shows a commit

**Containers:**
- [ ] `docker compose ps` shows all 6 containers with status `running` or `Up`
- [ ] `curl -s http://localhost:8099/health | python -m json.tool` returns `{"status": "healthy", ...}`
- [ ] `curl -s http://localhost:8000/health` returns 200
- [ ] `curl -s http://localhost:8001/health` returns 200
- [ ] `curl -s http://localhost:8002/health` returns 200
- [ ] `curl -s http://localhost:8003/health` returns 200
- [ ] `curl -s http://localhost:8004/health` returns 200

**Agent Cards:**
- [ ] `curl -s http://localhost:8001/.well-known/agent.json | python -m json.tool` returns valid JSON with a `skills` array containing 4 items
- [ ] `curl -s http://localhost:8002/.well-known/agent.json` — skills array contains 3 items
- [ ] `curl -s http://localhost:8003/.well-known/agent.json` — skills array contains 3 items
- [ ] `curl -s http://localhost:8004/.well-known/agent.json` — skills array contains 1 item (`risk_synthesis`)
- [ ] `curl -s http://localhost:8000/.well-known/agent.json` — skills array contains 1 item (`verify_subject`)
- [ ] `curl -s http://localhost:8000/agents` returns `[]`

**Logs:**
- [ ] `docker compose logs equifax 2>&1 | grep -i error` prints nothing
- [ ] `docker compose logs registry 2>&1 | grep -i error` prints nothing

**Commit:**
```bash
$ git add .
$ git commit -m "Phase 1: setup, skeletons, Agent Cards, docker-compose"
```
- [ ] Commit succeeds with no `.env` or `*.db` files included (`git show --name-only HEAD` confirms)

---

## Phase 2 — Agent Registry

**Goal:** The Registry service is fully implemented with SQLite persistence. All four data agents and the Orchestrator self-register on startup and deregister on graceful shutdown. The Orchestrator's `AgentResolver` can resolve any skill to an agent URL. `GET /orchestrator/agents` proxies the Registry.

**Estimated time:** 2–3 hours

---

### Step 2.1 — Implement the Registry Service

**File:** `agents/registry/main.py` — replace the Phase 1 stub with the full implementation.

**What it must do:**

*Database setup:*
- On startup, create `registry.db` at the path specified by `DB_PATH` env var (default `/data/registry.db`)
- Create table `registered_agents` with columns: `url_hash TEXT PRIMARY KEY`, `name TEXT NOT NULL`, `url TEXT NOT NULL UNIQUE`, `skills TEXT NOT NULL` (JSON array stored as text), `version TEXT`, `auth_schemes TEXT` (JSON), `capabilities TEXT` (JSON), `agent_card_raw TEXT NOT NULL`, `health TEXT DEFAULT 'unknown'`, `avg_latency_ms INTEGER`, `registered_at TEXT NOT NULL`, `last_seen TEXT`
- Use `sqlite3` (standard library), not `aiosqlite` — the Registry's endpoints are simple enough that synchronous SQLite is fine; no async DB calls needed

*`url_hash` generation:*
- `SHA-256(url)[:16]` — stable identifier across restarts, short enough to be readable in logs

*`POST /register` — agent self-registration:*
- Accept a `RegisterRequest` body
- Compute `url_hash` from the URL
- Use `INSERT OR REPLACE` — idempotent, re-registration after a container restart updates the row rather than raising a duplicate error
- Set `health = 'healthy'` and `registered_at = now()` on every registration
- Return `{ "url_hash": ..., "message": "Registered {name}" }` with status 201
- Print a log line: `[Registry] Registered: {name} @ {url} | skills: {skills}`

*`GET /agents` — list agents, optionally filter by skill:*
- Accept optional query param `skill: str = None`
- If `skill` is provided: `SELECT ... WHERE skills LIKE '%"{skill}"%'` — skills are stored as JSON arrays like `["credit_score", "tradelines"]`, so wrapping the skill in quotes makes the LIKE match precise (avoids `credit` matching `credit_score` and `international_credit_score`)
- If no skill: return all rows
- Return a list of `RegisteredAgent` objects
- Return `[]` (empty list, status 200) when no matches — never a 404

*`DELETE /agents/{url_hash}` — agent deregistration:*
- Delete the row matching `url_hash`
- Return `{ "message": "Deregistered {url_hash}" }` with status 200
- Return 404 if `url_hash` not found

*`GET /health`:*
- Return `{ "status": "healthy", "service": "registry", "port": 8099 }`

*FastAPI lifespan:*
- Call `init_db()` on startup before the app begins accepting requests
- Log `[Registry] Database initialised at {DB_PATH}`

---

### Step 2.2 — Add Self-Registration Lifespan to All Agents

Update `main.py` for all five agents (equifax, employment, intl, synthesis, orchestrator) to add a FastAPI `lifespan` context manager.

**Pattern for each agent:**

```
- Import asynccontextmanager, register_with_registry, deregister_from_registry from agents.shared.registry_client
- Declare a module-level variable: _url_hash: str | None = None
- Define lifespan(app):
    global _url_hash
    _url_hash = await register_with_registry()   # POSTs Agent Card to Registry
    yield                                         # app runs here
    await deregister_from_registry(_url_hash)     # DELETE on graceful shutdown
- Pass lifespan=lifespan to FastAPI(...)
```

All other endpoints remain unchanged from Phase 1. The lifespan is the only addition.

---

### Step 2.3 — Add `AgentResolver` to the Orchestrator

**File:** `agents/orchestrator/resolver.py` — new file.

**What it must do:**

`AgentResolver` class:
- `__init__`: reads `AGENT_REGISTRY_URL` from env (default `http://registry:8099`)
- `async find(skill: str) -> str`: queries `GET {registry_url}/agents?skill={skill}` using `httpx.AsyncClient` with a 5-second timeout. Filters results to agents with `health` in `("healthy", "unknown")`. Picks the one with the lowest `avg_latency_ms` (treat `None` as a large number so new agents with no latency data are tried last). Returns the agent's `url`. Raises `NoCandidateAgentError(skill)` if no healthy agent is found or the Registry is unreachable.
- `async find_all(skills: list[str]) -> dict[str, str]`: calls `find()` for each skill sequentially. Raises on the first unresolvable skill. Returns `{skill: url, ...}`.
- `async list_all() -> list[dict]`: queries `GET {registry_url}/agents` (no skill filter). Returns the raw list. Returns `[]` on any exception — this powers the `/agents` proxy endpoint and should never crash the Orchestrator.

`NoCandidateAgentError(Exception)`: simple exception class that takes a `skill: str` and formats a readable message.

---

### Step 2.4 — Update the Orchestrator's `/agents` Endpoint

**File:** `agents/orchestrator/main.py` — update the `GET /agents` stub.

- Import `AgentResolver`
- Instantiate `resolver = AgentResolver()` at module level (outside any route — shared across requests)
- Update `GET /agents` to return `await resolver.list_all()` instead of `[]`
- Add a debug endpoint `GET /resolve/{skill}` that calls `resolver.find(skill)` and returns `{"skill": skill, "url": url}` on success or `{"skill": skill, "error": str(e)}` on failure — useful for manual verification without writing tests

---

### Step 2.5 — Rebuild and Start

```bash
$ docker compose down        # stop the Phase 1 stack cleanly
$ docker compose up --build  # rebuild all images with Phase 2 changes
```

Watch the startup logs. You should see registration messages from each agent within a few seconds of the Registry becoming healthy:

```
verifyiq-equifax-1      | [Registry Client] Registered successfully — url_hash: a3f7b2c1...
verifyiq-employment-1   | [Registry Client] Registered successfully — url_hash: b4e8c3d2...
verifyiq-intl-1         | [Registry Client] Registered successfully — url_hash: c5f9d4e3...
verifyiq-synthesis-1    | [Registry Client] Registered successfully — url_hash: d6a0e5f4...
verifyiq-orchestrator-1 | [Registry Client] Registered successfully — url_hash: e7b1f6a5...
```

If you see `WARNING: Registration failed`, the Registry's health check may not have passed before the agent tried to register. The `depends_on: condition: service_healthy` in `docker-compose.yml` should prevent this, but if it occurs check `docker compose logs registry` for startup errors.

---

### Step 2.6 — Write Phase 2 Tests

**File:** `tests/test_phase2_registry.py`

This is an integration test file run against the live Docker Compose stack. Use `pytest` + `httpx` (async client) + `pytest-asyncio`.

**Test cases to implement** (one test function per case):

1. **`test_equifax_registered`** — `GET /agents?skill=credit_score` returns exactly 1 agent; its name is `"Equifax Credit Agent"` and url is `"http://equifax:8001"`.

2. **`test_all_five_agents_registered`** — `GET /agents` (no filter) returns 5 agents. Verify all five URLs are present: equifax:8001, employment:8002, intl:8003, synthesis:8004, orchestrator:8000.

3. **`test_skill_filter_employment`** — `GET /agents?skill=employment_status` returns exactly 1 agent with name `"Employment Verification Agent"`.

4. **`test_skill_filter_synthesis`** — `GET /agents?skill=risk_synthesis` returns exactly 1 agent.

5. **`test_nonexistent_skill_returns_empty`** — `GET /agents?skill=nonexistent_xyz_skill` returns `[]` with status 200 (not 404, not 500).

6. **`test_registration_is_idempotent`** — POST the same payload to `/register` twice. Then `GET /agents?skill=dup_test_skill` returns exactly 1 agent (upsert, not duplicate). Clean up by calling `DELETE /agents/{url_hash}` afterward.

7. **`test_deregister_removes_agent`** — Register a temporary agent, verify it appears, DELETE it, verify it no longer appears in the list.

8. **`test_registry_persists_across_restart`** — Record the count of agents. Restart the Registry container: `subprocess.run(["docker", "compose", "restart", "registry"])`, sleep 5 seconds, query again. Count must be the same.

9. **`test_orchestrator_proxies_registry`** — `GET http://localhost:8000/agents` returns the same set of agent URLs as `GET http://localhost:8099/agents`.

10. **`test_orchestrator_resolve_debug_endpoint`** — `GET http://localhost:8000/resolve/credit_score` returns `{"skill": "credit_score", "url": "http://equifax:8001"}`.

11. **`test_orchestrator_resolve_unknown_skill`** — `GET http://localhost:8000/resolve/no_such_skill` returns a response containing an `"error"` key (not a 500).

**Test file structure:**

```python
# Base URLs as module-level constants
REGISTRY_URL = "http://localhost:8099"
ORCHESTRATOR_URL = "http://localhost:8000"

# Mark the module for asyncio: pytestmark = pytest.mark.asyncio

# Each test is async and uses httpx.AsyncClient as a context manager
```

**`tests/__init__.py`** — empty file to make the directory a package.

**`tests/conftest.py`** — optional but recommended: define a shared `async_client` fixture if multiple tests need it, or just use `httpx.AsyncClient` inline.

---

### Step 2.7 — Install Test Dependencies and Run Tests

```bash
$ pip install pytest pytest-asyncio httpx
$ pytest tests/test_phase2_registry.py -v
```

All 11 tests must pass. If any fail, check `docker compose logs registry` and `docker compose logs <agent-name>` for errors before debugging the test itself.

---

### Step 2.8 — Verify Registry Persistence Manually

This verifies the SQLite volume survives a Registry restart without running a test:

```bash
# Count agents before restart
$ curl -s http://localhost:8099/agents | python3 -c \
    "import sys,json; print(len(json.load(sys.stdin)), 'agents')"

# Restart only the Registry container
$ docker compose restart registry
$ sleep 6

# Count again — must be the same
$ curl -s http://localhost:8099/agents | python3 -c \
    "import sys,json; print(len(json.load(sys.stdin)), 'agents')"
```

Expected: same count both times (5). If the count drops to 0, the `registry_data` volume is not being mounted correctly in `docker-compose.yml`.

---

### Step 2.9 — Verify Auto Re-Registration After Agent Restart

```bash
# Restart the Equifax Agent
$ docker compose restart equifax
$ sleep 6

# Should still be exactly 1 agent for credit_score — no duplicate
$ curl -s "http://localhost:8099/agents?skill=credit_score" | python3 -c \
    "import sys,json; a=json.load(sys.stdin); print(len(a), 'agent(s) for credit_score')"
```

Expected: `1 agent(s) for credit_score`. If you see 2, the `INSERT OR REPLACE` logic in the Registry is not working — the upsert is creating a new row instead of replacing.

---

### Step 2.10 — Inspect the Registry Database Directly

The `sqlite3` CLI is not installed in the container. Use Python instead:

```bash
$ docker compose exec registry python -c "
import sqlite3
conn = sqlite3.connect('/data/registry.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute('SELECT name, health, registered_at FROM registered_agents')
for row in cur.fetchall():
    print(dict(row))
"
```

Expected: 5 rows, all with `health = healthy`.

```bash
$ docker compose exec registry python -c "
import sqlite3
conn = sqlite3.connect('/data/registry.db')
cur = conn.cursor()
cur.execute(\"SELECT name, skills FROM registered_agents WHERE skills LIKE '%credit_score%'\")
print(cur.fetchall())
"
```

Expected: 1 row — the Equifax Agent.

---

### Step 2.11 — Commit Phase 2

```bash
$ git add .
$ git commit -m "Phase 2: Agent Registry with SQLite, self-registration, AgentResolver"
```

---

### ✅ Phase 2 Verification Checklist

- [ ] `docker compose up --build` completes without errors after Phase 2 changes
- [ ] Startup logs show `Registered successfully` for all 5 agents (check with `docker compose logs | grep "Registered successfully"`)
- [ ] `curl -s http://localhost:8099/agents | python3 -c "import sys,json; print(len(json.load(sys.stdin)))"` prints `5`
- [ ] Each of the 5 agent objects has `name`, `url`, `skills` (array), `health: "healthy"`, `registered_at`
- [ ] `curl -s "http://localhost:8099/agents?skill=credit_score"` returns exactly 1 agent (Equifax)
- [ ] `curl -s "http://localhost:8099/agents?skill=employment_status"` returns exactly 1 agent (Employment)
- [ ] `curl -s "http://localhost:8099/agents?skill=international_credit_score"` returns exactly 1 agent (Intl)
- [ ] `curl -s "http://localhost:8099/agents?skill=risk_synthesis"` returns exactly 1 agent (Synthesis)
- [ ] `curl -s "http://localhost:8099/agents?skill=nonexistent"` returns `[]` (not an error)
- [ ] `curl -s "http://localhost:8000/resolve/credit_score"` returns `{"skill": "credit_score", "url": "http://equifax:8001"}`
- [ ] `curl -s "http://localhost:8000/resolve/no_such_skill"` returns a JSON object with an `"error"` key (not a 500)
- [ ] `curl -s http://localhost:8000/agents` returns the same 5 agents as `curl http://localhost:8099/agents`
- [ ] Registry persistence: `docker compose restart registry && sleep 6 && curl -s http://localhost:8099/agents` still returns 5 agents
- [ ] Auto re-registration: `docker compose restart equifax && sleep 6 && curl -s "http://localhost:8099/agents?skill=credit_score"` returns exactly 1 agent (not 2)
- [ ] `docker compose exec registry sqlite3 /data/registry.db "SELECT name FROM registered_agents;"` returns 5 names
- [ ] `pytest tests/test_phase2_registry.py -v` — all 11 tests pass
- [ ] `git log --oneline | head -2` shows Phase 2 commit on top

---

*Document: VerifyIQ Implementation Guide | Phases 1–2 | Companion to Spec v1.7*
*Next: Phase 3 — A2A Task Protocol*
