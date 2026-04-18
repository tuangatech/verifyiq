# VerifyIQ — CLAUDE.md

Multi-agent credit/employment verification platform. Learning project for A2A
orchestration patterns — do not simplify away the complexity; it is the point.

---

## Architecture

```
Mortgage Platform (:9000)  ← UC-5 external A2A caller (Phase 9 stretch)
        │ A2A POST /tasks/send
Orchestrator (:8000)       ← ADK + FastAPI hub
        │ GET /agents?skill=
Agent Registry (:8099)     ← FastAPI; self-registration + skill discovery
        ▲ POST /register on startup
Equifax (:8001) · Employment (:8002) · Intl (:8003) · Synthesis (:8004)
CLI (host)                 ← typer + rich; calls Orchestrator HTTP/SSE
```

All inter-agent comms: `POST /tasks/send`. No shared DBs, no cross-service imports.

## Tech Stack

| Layer | Tech |
|---|---|
| CLI | Python + typer + rich + httpx |
| Orchestrator | Python + FastAPI + ADK |
| Data agents | Python + FastAPI (Employment: LangGraph) |
| LLM routing | OpenRouter — `google/gemini-3-flash` (agents), `openai/gpt-5.4-mini` (synthesis) |
| Shared schemas | Pydantic in `agents/shared/` |
| Databases | SQLite — `verifyiq.db` (orchestrator), `registry.db` (registry) |
| Packaging | `uv`, Docker + Docker Compose |

## Repository Layout

```
agents/
  shared/         # schemas.py (artifacts), a2a_types.py (protocol types)
  orchestrator/   # :8000  registry/   # :8099
  equifax/        # :8001  employment/ # :8002  intl/ # :8003  synthesis/ # :8004
cli/              # Python CLI (typer + rich); VERIFYIQ_URL env var
mortgage-platform/  # Phase 9 only
docker-compose.yml · .env.example
```

Each agent: its own `requirements.txt`, `Dockerfile`, `main.py`, `tools.py`, `prompts.py`.
Common deps (FastAPI, httpx, pydantic) repeated intentionally — agent independence > DRY.

## Environment

```
OPENROUTER_API_KEY=
OPENROUTER_AGENT_MODEL=google/gemini-3-flash
OPENROUTER_SYNTHESIS_MODEL=openai/gpt-5.4-mini
AGENT_REGISTRY_URL=http://registry:8099
AGENT_TIMEOUT_SECONDS=30
```

Swap models via env vars only. Never hardcode model names, ports, or URLs in code.

---

## Core Design Principles

1. **Organizational boundary rule** — Crosses org/deploy boundary → A2A. Within one agent with branching → LangGraph. Simple internal ops → plain Python functions.
2. **A2A as exclusive transport** — All inter-service calls go through `POST /tasks/send`. No hardcoded agent URLs in Orchestrator; all resolution via `AgentResolver` → Registry.
3. **Thin orchestrator, fat agents** — Orchestrator: discovery, dispatch, fan-out, chain, SSE. Zero domain logic.
4. **Fail-partial, not fail-all** — Always produce a report. Risk Synthesis gets `AgentOutcome` objects with failure context, not just successful artifacts.
5. **Correlation IDs end-to-end** — `correlation_id` in every task, SSE event, and SQLite row. One grep traces any request across all containers.
6. **Internal tools = plain Python functions** — Named, typed, single-responsibility. Same pattern as MCP tools, without the wire protocol.

---

## A2A Protocol

**Task lifecycle:** `submitted → working → completed | failed | timed_out` · `skipped` (not invoked)

`timed_out` ≠ `failed` — Risk Synthesis prompts treat them differently. Never collapse them.

**Key types** (`agents/shared/a2a_types.py`):
- `A2ATask`: `task_id`, `correlation_id`, `skill`, `input`, `timeout_ms=30000`, `attempt=1`
- `A2ATaskResult`: `task_id`, `correlation_id`, `status`, `artifact|None`, `error|None`, `started_at`, `ended_at`
- `AgentError`: `code`, `message`, `retryable` (True=transient, False=logic error)
- `AgentOutcome`: `agent_name`, `skill`, `status`, `artifact|None`, `error|None`

**Retry policy** — Network: 1 retry on 5xx/connection error after 1s; `attempt` 1→2; second failure → `AgentError`. LLM validation: 1 retry with error appended to prompt; second failure → `AgentError(code="LLM_VALIDATION_FAILED", retryable=False)`. These are independent loops.

**Self-registration** — Each agent POSTs its Agent Card to Registry on startup (`lifespan`), sends `DELETE` on graceful shutdown. Agent Cards at `/.well-known/agent.json`.

---

## LLM Integration

Structured output pattern (all data agents): system prompt = persona + schema → user prompt = subject seed → LLM returns raw JSON → validate Pydantic → 1 retry on failure → `AgentError` on second failure.

Risk Synthesis: Temperature 0.2. Prompt explicitly distinguishes `completed` / `failed` / `timed_out` outcomes. Never flatten failure context before passing to LLM. Use-case-weighted: `auto` weights DTI; `mortgage` weights all equally.

---

## Testing

Write tests **before** moving to the next phase.

**Priority:** Orchestration patterns (Phase 5, most critical) → A2A protocol round-trips (Phase 3) → Registry (Phase 2) → SSE streaming (Phase 6) → E2E flows (Phase 7).

**Tools:** `pytest`, `httpx` async, `pytest-asyncio`, `respx` (mock OpenRouter only).

**Do not mock** the Registry or the A2A protocol in integration tests — only mock OpenRouter HTTP calls.

---

## Coding Standards

- Type-annotate all signatures. Pydantic at all service boundaries — no raw `dict` passing.
- Every file starts with `# <relative-path>` (e.g. `# agents/equifax/main.py`).
- Every function/method gets a 1–2 line docstring. No novel-length descriptions.
- Complex logic (branching, retries, non-obvious flows) gets inline `#` comments. Obvious code does not.
- `structlog` JSON logging: every event includes `correlation_id`, `task_id`, `agent`, `duration_ms`.
- `asyncio.gather` for fan-out. `httpx.AsyncClient` for all outbound HTTP.
- CLI: no path dependency on `agents/shared/`; lightweight response models in `cli/verifyiq_cli/models.py`.
- No auth between agents until Phase 8.

---

## Build Sequence

| Phase | Goal |
|---|---|
| 1 | Skeletons — all containers start, `/health` + `/.well-known/agent.json` respond |
| 2 | Agent Registry — registration, skill discovery, SQLite persistence |
| 3 | A2A task protocol — full round-trip with stub artifacts, `correlation_id` in SQLite |
| 4 | LLM artifact generation — OpenRouter calls, Pydantic validation, retry |
| 5 | Orchestration patterns — fan-out, timeouts, retries, optional agents, sequential chain |
| 6 | LangGraph Employment Agent + SSE streaming |
| 7 | CLI — `verifyiq run/agents/history/inspect`; new Orchestrator endpoints |
| 8 | Auth + polish (stretch) |
| 9 | ADK server-side + Mortgage Platform UC-5 (stretch) |

## Use Cases

| UC | Agents | Pattern |
|---|---|---|
| UC-1a | Equifax + Employment + Intl + Synthesis | Full four-agent; intl data available |
| UC-1b | Equifax + Employment + Intl + Synthesis | Intl returns `unavailable` — graceful degradation |
| UC-2 | Equifax + Employment + Synthesis | Intl skipped entirely |
| UC-3 | Equifax + Employment + Synthesis | DTI-weighted synthesis |
| UC-4 | Intl + Employment + Synthesis | Equifax skipped |
| UC-5 | Equifax + Employment + Intl + Synthesis | VerifyIQ as A2A server (Phase 9) |

---

## Decisions Not to Re-Litigate

- **SQLite** — zero ops; single writer per DB; swap conn string for cloud migration
- **Plain Python functions** over MCP servers — same pattern, no wire protocol overhead
- **LangGraph in Employment only** — framework-agnostic interop without LangGraph-heavy orchestration
- **ADK server-side only** — earns its cost on UC-5 inbound protocol; outbound uses plain `httpx`
- **Global timeout** — per-skill config adds complexity without value at this scale

## Common Pitfalls

- `agents/shared/` types are **copied into containers at build time** — not a runtime shared lib
- Never collapse `timed_out` into `failed` — they carry different meaning in synthesis prompts
- No agent URL literals in Orchestrator logic — all discovery through `AgentResolver` → Registry
- Phase 3 uses stub artifacts deliberately — isolates LLM bugs from protocol bugs in Phase 4
- `mortgage-platform` starts explicitly only (`docker compose up mortgage-platform`) — not default

## Approach
- Think before acting. Read existing files before writing code.
- Be concise in output but thorough in reasoning.
- Prefer editing over rewriting whole files.
- Do not re-read files you have already read unless the file may have changed.
- Test your code before declaring done.
- No sycophantic openers or closing fluff.
- Keep solutions simple and direct.
- User instructions always override this file.