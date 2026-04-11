# VerifyIQ — CLAUDE.md

A multi-agent credit and employment verification platform built to learn and practice
Agent-to-Agent (A2A) orchestration patterns. This document is the authoritative guide
for working in this repository.

---

## Project Overview

VerifyIQ simulates a real-world verification ecosystem where a loan officer submits a
request and multiple independent AI agents collaborate to produce a unified report.
Each agent models a separate organization (Equifax, The Work Number, Experian
International) and communicates exclusively through the A2A HTTP protocol.

**This is a learning project.** Every architectural decision serves a specific learning
objective. Do not simplify away the complexity — the complexity is the point.

---

## Architecture at a Glance

```
Mortgage Platform (:9000)   ← UC-5: external A2A caller (stretch Phase 9)
        │ A2A POST /tasks/send
        ▼
Orchestrator (:8000)        ← ADK + FastAPI; hub agent
        │ GET /agents?skill=
        ▼
Agent Registry (:8099)      ← FastAPI; self-registration + skill discovery
        ▲ POST /register (on startup)
        │
Equifax Agent  (:8001)      ← FastAPI + OpenRouter (gemini-3-flash)
Employment Agent (:8002)    ← LangGraph + OpenRouter (gemini-3-flash)
Intl Agent     (:8003)      ← FastAPI + OpenRouter (gemini-3-flash)
Risk Synthesis (:8004)      ← FastAPI + OpenRouter (gpt-5.4-mini)
        │
Next.js UI (:3000)          ← App Router, Tailwind, shadcn/ui, native EventSource
```

All inter-agent communication uses `POST /tasks/send` (A2A). No shared code, no shared
databases, no direct imports across service boundaries.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 14 (App Router), Tailwind CSS, shadcn/ui |
| Frontend streaming | Native `EventSource` API (SSE) |
| Orchestrator | Python + FastAPI + ADK |
| Data agents | Python + FastAPI |
| Employment agent | Python + LangGraph |
| Agent Registry | Python + FastAPI (~120 lines) |
| LLM routing | OpenRouter (single key, model strings in env vars) |
| Data agent LLM | `google/gemini-3-flash` |
| Synthesis LLM | `openai/gpt-5.4-mini` |
| Shared schemas | Pydantic (in `agents/shared/`) |
| Databases | SQLite (`verifyiq.db` for orchestrator, `registry.db` for registry) |
| Packaging | `uv` (Python), Docker + Docker Compose |

---

## Repository Layout

```
verifyiq/
├── agents/
│   ├── shared/
│   │   ├── schemas.py          # Domain artifact types (Pydantic)
│   │   └── a2a_types.py        # A2A protocol types (A2ATask, A2ATaskResult, etc.)
│   ├── orchestrator/           # :8000 — ADK + FastAPI hub
│   ├── registry/               # :8099 — Agent Registry
│   ├── equifax/                # :8001 — Credit agent
│   ├── employment/             # :8002 — LangGraph employment agent
│   ├── intl/                   # :8003 — International credit agent
│   └── synthesis/              # :8004 — Risk Synthesis agent
├── ui/                         # Next.js 14 frontend (:3000)
├── mortgage-platform/          # :9000 — UC-5 external caller (Phase 9)
├── docker-compose.yml
├── .env.example
└── CLAUDE.md
```

Each agent directory contains its own `requirements.txt`, `Dockerfile`, and `main.py`.
Common dependencies (FastAPI, uvicorn, httpx, pydantic) are repeated intentionally —
preserving agent independence matters more than DRY across service boundaries.

**Dependency versions:** Pin to the latest stable PyPI releases. Check before creating or
updating `requirements.txt` files — do not copy versions from older documentation without
verifying they are still current. Use `pip index versions <package>` or PyPI to confirm.

---

## Environment Setup

### Prerequisites

- Docker Desktop
- Node.js 20+
- Python 3.12+
- `uv` (Python package manager)
- SQLite CLI
- OpenRouter account with API key

### Environment Variables

One `.env` at repository root. Never commit it. `.env.example` is committed.

```
OPENROUTER_API_KEY=
OPENROUTER_AGENT_MODEL=google/gemini-3-flash
OPENROUTER_SYNTHESIS_MODEL=openai/gpt-5.4-mini
AGENT_REGISTRY_URL=http://registry:8099
AGENT_TIMEOUT_SECONDS=30
```

Swap models by changing env vars only — no code changes.

### Running the Stack

```bash
# Start all services
docker compose up

# Start a single agent locally (rapid iteration)
# Set AGENT_REGISTRY_URL=http://localhost:8099 first
cd agents/equifax && uvicorn main:app --port 8001

# UC-5: start Mortgage Platform explicitly (does not run by default)
docker compose up mortgage-platform

# Inspect databases
sqlite3 verifyiq.db
sqlite3 registry.db

# Trace a full request across all containers
docker compose logs | grep "<correlation_id>"
```

---

## Core Design Principles

### 1. The Organizational Boundary Rule

Every technology choice at the inter-agent level follows one question:
**Does this cross an organizational or deployment boundary?**

- Across org/deployment boundaries → **A2A**
- Within one agent, with branching state → **LangGraph** (internal only)
- Within one agent, simple operations → **plain Python functions**

This rule is applied consistently throughout VerifyIQ. Do not bend it.

### 2. A2A as the Exclusive Inter-Agent Transport

All inter-service communication goes through `POST /tasks/send` and `GET /tasks/{id}`.
No shared databases between services. No direct Python imports across service boundaries.
No hardcoded agent URLs in the Orchestrator — all resolution goes through the Registry.

### 3. Thin Orchestrator, Fat Agents

The Orchestrator handles: discovery, dispatch, collection, chaining, SSE emission.
Zero domain logic. Each agent owns its domain logic, LLM prompting, and internal tooling.

### 4. Fail-Partial, Not Fail-All

The Orchestrator always produces a report, even when agents fail, time out, or return
no data. Risk Synthesis receives structured `AgentOutcome` objects — including failure
context — not just successful artifacts.

### 5. Correlation IDs Flow End-to-End

Every A2A task carries a `correlation_id` generated at request intake. It flows into
every agent task, every SSE event, and every SQLite row. A single grep reconstructs
the complete trace of any request without a distributed tracing system.

### 6. Internal Tools Are Plain Python Functions

Each agent has internal helper functions. These are named, single-responsibility, and
typed — the same pattern as MCP tools — but implemented as plain Python without the
wire protocol. Keep focus on A2A as the learning target.

---

## A2A Protocol Contracts

### Key Types (in `agents/shared/a2a_types.py`)

```python
class A2ATask(BaseModel):
    task_id: str
    correlation_id: str       # flows unchanged end-to-end
    skill: str
    input: dict[str, Any]
    timeout_ms: int = 30000
    attempt: int = 1          # 1 = first attempt, 2 = retry

class A2ATaskResult(BaseModel):
    task_id: str
    correlation_id: str
    status: Literal["completed", "failed", "timed_out"]
    artifact: dict[str, Any] | None = None
    error: AgentError | None = None
    started_at: str           # ISO datetime
    ended_at: str             # ISO datetime

class AgentError(BaseModel):
    code: str                 # e.g. "LLM_VALIDATION_FAILED", "TIMEOUT"
    message: str
    retryable: bool           # True: transient; False: logic error

class AgentOutcome(BaseModel):
    agent_name: str
    skill: str
    status: Literal["completed", "failed", "timed_out", "skipped"]
    artifact: dict[str, Any] | None = None
    error: AgentError | None = None
```

### Task Lifecycle

```
submitted → working → completed
                    → failed       (agent error; retry exhausted)
                    → timed_out    (no response within timeout_ms)
          → skipped                (agent not invoked for this use case)
```

`timed_out` and `failed` are meaningfully distinct. Risk Synthesis receives different
context for each. Do not collapse them into a generic error state.

### Agent Self-Registration

Every agent registers on startup via FastAPI `lifespan`:
- `POST /register` to Registry with its Agent Card on startup
- `DELETE /agents/{url_hash}` on graceful shutdown

Agent Cards are served at `/.well-known/agent.json`. No agent URLs are hardcoded in
the Orchestrator.

### Retry Policy (TaskDispatcher)

- 1 retry on HTTP 5xx or connection error, after a 1-second delay
- `attempt` field incremented on retry (1 → 2)
- On second failure: return `AgentError(code="...", retryable=True)`
- LLM validation failure (within an agent): 1 retry with error context appended to prompt
- These are independent retry loops — do not conflate them

---

## LLM Integration

### Structured Output Pattern (all data agents)

1. System prompt defines persona + output schema
2. User prompt provides subject seed
3. LLM returns raw JSON only (`response_format: { type: "json_object" }` where supported)
4. Validate against Pydantic model
5. On failure: one retry with validation error appended to prompt
6. On second failure: return `AgentError(code="LLM_VALIDATION_FAILED", retryable=False)`

### Risk Synthesis Prompt

The prompt explicitly distinguishes outcome statuses — `completed`, `failed`, and
`timed_out` warrant meaningfully different confidence statements. Never flatten failure
context before passing to the synthesis LLM.

Temperature: 0.2. System prompt varies by use case type (`auto` weights DTI more;
`mortgage` weights all sources equally).

### Model Configuration

All model strings live in env vars. Never hardcode model names in agent logic.

---

## Testing

Tests are written **before moving to the next phase**. Each phase has a goal — tests
verify it.

### Test Tooling

```
pytest                # all unit and integration tests
httpx (async)         # test FastAPI endpoints
pytest-asyncio        # async test support
respx                 # mock OpenRouter calls in unit tests
```

### What to Test (Priority Order)

1. **Orchestration patterns** (Phase 5 tests) — most critical; fan-out concurrency,
   timeout/retry handling, optional agent skip/unavailable, failure passthrough to
   synthesis, `correlation_id` continuity
2. **A2A protocol round-trips** (Phase 3 tests) — all required fields present, status
   transitions written to SQLite
3. **Agent Registry** (Phase 2 tests) — registration, skill filtering, deduplication,
   SQLite persistence
4. **SSE streaming** (Phase 6 tests) — event order, `correlation_id` in every event,
   disconnect safety
5. **End-to-end flows** (Phase 7 tests) — UC-1a through UC-4 each exercise a distinct
   agent combination

### Do Not Mock

- **Do not mock the Registry** in orchestration integration tests — the Registry is
  infrastructure, not a dependency to abstract away
- **Do not mock the A2A protocol** between Orchestrator and agents in integration tests
- Use `respx` to mock **OpenRouter HTTP calls only**

---

## Coding Standards

### Python

- Python 3.11+; use `uv` for all package management
- Type-annotate all function signatures
- Pydantic for all data validation — no `dict` passing at service boundaries
- `structlog` for JSON-structured logging; every log event includes `correlation_id`,
  `task_id`, `agent`, and `duration_ms` where applicable
- Use `asyncio.gather` for parallel fan-out; never serialize work that can run concurrently
- Each agent's `main.py` uses FastAPI `lifespan` for startup/shutdown hooks
- `httpx.AsyncClient` for all outbound HTTP (including A2A dispatch)

### Agent Structure (Python services)

```
agents/<name>/
├── main.py          # FastAPI app, lifespan hook, /tasks/send, /.well-known/agent.json
├── tools.py         # Internal plain Python functions (named, typed, single-responsibility)
├── prompts.py       # LLM prompt strings and builders
├── requirements.txt # Agent-local deps (intentionally not shared)
└── Dockerfile
```

### TypeScript / Next.js

- Next.js 14 App Router; use server components by default, client only where necessary
- Tailwind CSS for styling; shadcn/ui for components
- Native `EventSource` API for SSE — no third-party SSE library
- `fetch` for API calls; no Axios

### General

- No hardcoded URLs, ports, model names, or timeout values — all from env vars or config
- No cross-service Python imports — shared types live in `agents/shared/` and are
  copied into each container at build time via Dockerfile, not imported at runtime
- No auth between agents until Phase 8 (bearer tokens via env vars)
- No Prometheus, Jaeger, or ELK — `docker compose logs | grep <correlation_id>` is
  sufficient for a local project

---

## Build Sequence Reference

Build one phase at a time. Do not start the next phase until all tests for the current
phase pass.

| Phase | Focus | Time |
|---|---|---|
| 1 | Setup, skeletons, Agent Cards — all containers start, `/health` + `/.well-known/agent.json` respond | 3–5h |
| 2 | Agent Registry — self-registration, skill-based discovery, SQLite persistence | 2–3h |
| 3 | A2A task protocol — full round-trip with stub artifacts, `correlation_id` in SQLite | 3–4h |
| 4 | LLM artifact generation — real OpenRouter calls, Pydantic validation, one retry | 2–3h |
| 5 | Orchestration patterns — fan-out, timeouts, retries, optional agents, sequential chain | 3–4h |
| 6 | LangGraph Employment Agent + SSE streaming | 4–5h |
| 7 | Next.js frontend — form, live report, agent dashboard, history | 4–6h |
| 8 | Auth + polish (stretch) — bearer tokens, structlog, README, persona fixtures | — |
| 9 | ADK server-side + Mortgage Platform UC-5 (stretch) | — |

---

## Use Case Reference

| UC | Entry point | Agents invoked | Key pattern |
|---|---|---|---|
| UC-1a | Browser `POST /verify` | Equifax + Employment + Intl + Synthesis | Full four-agent, intl data available |
| UC-1b | Browser `POST /verify` | Equifax + Employment + Intl + Synthesis | Intl returns `unavailable` (graceful degradation) |
| UC-2 | Browser `POST /verify` | Equifax + Employment + Synthesis | Intl skipped entirely |
| UC-3 | Browser `POST /verify` | Equifax + Employment + Synthesis | DTI-weighted synthesis for auto |
| UC-4 | Browser `POST /verify` | Intl + Employment + Synthesis | Equifax skipped entirely |
| UC-5 | Mortgage Platform `POST /tasks/send` | Equifax + Employment + Intl + Synthesis | VerifyIQ as A2A server (Phase 9) |

---

## SQLite Schema Reference

### `verifyiq.db` (Orchestrator)

- `verification_requests` — one row per request; `id` = `task_id`; includes
  `correlation_id`, `use_case`, `status`, `decision`, `started_at`, `completed_at`
- `agent_tasks` — one row per A2A task dispatched; includes `correlation_id`, `attempt`,
  `started_at`, `ended_at`, `status`, full input payload, output artifact or error
- `sse_events` — one row per SSE event emitted; includes `correlation_id`, `event_type`,
  full JSON payload

### `registry.db` (Registry)

- `registered_agents` — one row per agent; keyed by URL hash; stores full Agent Card JSON,
  skills array, `health`, `avg_latency_ms`, `last_seen`

---

## Key Decisions Not to Re-Litigate

- **SQLite over PostgreSQL** — zero ops for local dev; single writer per DB; swap connection
  string only for cloud migration
- **Plain Python functions over MCP servers** — same pattern, no wire protocol overhead;
  keeps A2A as the sole inter-agent protocol focus
- **LangGraph in Employment Agent only** — demonstrates framework-agnostic A2A interop
  without making the whole project LangGraph-heavy
- **ADK on server side only** — earns its cost managing the UC-5 inbound task protocol;
  outbound dispatch uses plain `httpx`
- **Global `AGENT_TIMEOUT_SECONDS`** — per-skill timeouts add config complexity without
  proportional value at this scale
- **Single repository** — appropriate for a side project; Docker container boundaries
  enforce service independence

---

## Common Pitfalls

- **Do not import across service boundaries.** `agents/shared/` types are copied into
  containers at Docker build time. They are not a shared library imported at runtime.
- **Do not collapse `timed_out` into `failed`.** They are distinct states with different
  meaning in the Risk Synthesis prompt.
- **Do not hardcode agent URLs.** All agent discovery goes through `AgentResolver` →
  Registry. There must be no URL string literals pointing to agent services in the
  Orchestrator's business logic.
- **Do not run the Mortgage Platform by default.** UC-5 is a stretch goal. Start it
  explicitly with `docker compose up mortgage-platform`.
- **Do not skip the stub phase.** Phase 3 uses hardcoded artifacts deliberately so that
  if something breaks in Phase 4, you know it is the LLM and not the protocol.


---------
## Workflow Orchestration

### 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately - don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes - don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests - then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management

1. **Plan First**: Write plan to `tasks/todo.md` with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section to `tasks/todo.md`
6. **Capture Lessons**: Update `tasks/lessons.md` after corrections

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.