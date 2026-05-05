# VerifyIQ

Multi-agent credit and employment verification platform built to learn **Agent-to-Agent (A2A) orchestration patterns**.

## Architecture

```
Mortgage Platform (:9000)  ← UC-5 external A2A caller
        │ POST /tasks/send
CLI (host)  -->  Orchestrator (:8000)  -->  Agent Registry (:8099)
                       |
          +------------+------------+
          |            |            |
     Equifax      Employment     Intl        -->  Risk Synthesis
     (:8001)      (:8002)       (:8003)           (:8004)
```

- **Orchestrator** -- discovery, fan-out, chaining, SSE streaming. No domain logic.
- **Data agents** -- each independently deployed, owns its own LLM prompts and artifact schema.
- **Risk Synthesis** -- receives structured outcomes (including failures) and produces a final decision.
- **Agent Registry** -- self-registration and skill-based discovery.

All inter-agent communication goes through `POST /tasks/send` (A2A protocol). No shared DBs or cross-service imports.

## Tech Stack

| Layer | Tech |
|---|---|
| CLI | Python, typer, rich, httpx |
| Orchestrator | FastAPI |
| Data agents | FastAPI (Employment: LangGraph) |
| LLM routing | OpenRouter (`gemini-3-flash`, `gpt-5.4-mini`) |
| Schemas | Pydantic (`agents/shared/`) |
| Databases | SQLite |
| Packaging | Docker Compose, uv |

## Quick Start

```bash
# 1. Clone and enter the project
cd verifyiq

# 2. Environment
cp .env.example .env
# Fill in OPENROUTER_API_KEY

# 3. Start services
docker compose down && docker compose up --build -d

# 4. Start Mortgage Platform (UC-5)
docker compose --profile uc5 up --build -d

# 5. Install CLI
uv pip install -e cli/

# 6. Run verifications
verifyiq run mortgage-intl
verifyiq run mortgage-platform
```

## Use Cases

| Command | Scenario | Agents |
|---|---|---|
| `verifyiq run mortgage-intl` | Mortgage, intl data available | Equifax + Employment + Intl + Synthesis |
| `verifyiq run mortgage-domestic` | Mortgage, intl unavailable | Equifax + Employment + Intl + Synthesis |
| `verifyiq run rental` | Rental screening | Equifax + Employment + Synthesis |
| `verifyiq run auto` | Auto loan (DTI-weighted) | Equifax + Employment + Synthesis |
| `verifyiq run hire` | International hire | Intl + Employment + Synthesis |
| `verifyiq run mortgage-platform` | UC-5: External A2A caller | Mortgage Platform → Orchestrator → all agents |

## Other CLI Commands

```bash
verifyiq agents              # List registered agents
verifyiq history             # Past verification requests
verifyiq inspect <task_id>   # Full artifact dump
```

## Environment Variables

| Variable | Default |
|---|---|
| `OPENROUTER_API_KEY` | (required) |
| `OPENROUTER_AGENT_MODEL` | `google/gemini-3-flash` |
| `OPENROUTER_SYNTHESIS_MODEL` | `openai/gpt-5.4-mini` |
| `AGENT_REGISTRY_URL` | `http://registry:8099` |
| `AGENT_TIMEOUT_SECONDS` | `30` |

## Project Structure

```
agents/
  shared/            # Pydantic schemas and A2A types
  orchestrator/      # :8000 -- hub agent (caller + callee)
  registry/          # :8099 -- agent discovery
  equifax/           # :8001 -- credit bureau
  employment/        # :8002 -- LangGraph employment verification
  intl/              # :8003 -- international credit
  synthesis/         # :8004 -- risk decision
mortgage-platform/   # :9000 -- external A2A caller (UC-5)
cli/                 # Python CLI package
```

## How It Works

1. **Request** -- CLI sends `POST /verify` to the Orchestrator with a subject and use case type.
2. **Discovery** -- Orchestrator queries the Agent Registry by skill to find which agents to call.
3. **Fan-out** -- Data agents (Equifax, Employment, Intl) are called in parallel via `asyncio.gather`. Each agent calls an LLM (via OpenRouter) to generate a realistic artifact, validates it with Pydantic, and retries once on validation failure.
4. **Collect** -- Results are gathered as `AgentOutcome` objects that preserve failure context (`completed`, `failed`, `timed_out`, `skipped`).
5. **Chain** -- The full outcome bundle is passed sequentially to Risk Synthesis, which reasons over all evidence -- including failures -- to produce a `VerificationDecision`.
6. **Stream** -- SSE events are emitted at each milestone so the CLI can show live progress.

## A2A Protocol

All inter-agent communication uses a simple task protocol:

- **Task lifecycle:** `submitted -> working -> completed | failed | timed_out` (or `skipped` if not invoked)
- **Retry policy:** 1 network retry on 5xx/connection error; 1 LLM retry on validation failure
- **Self-registration:** Each agent POSTs its Agent Card to the Registry on startup, DELETEs on shutdown
- **Correlation IDs:** Every task, SSE event, and SQLite row carries a `correlation_id` for end-to-end tracing

## Key Design Decisions

- **A2A for inter-agent, LangGraph for intra-agent** -- crosses an org boundary? A2A. Stateful branching within one agent? LangGraph. Simple internal ops? Plain Python functions.
- **Thin orchestrator, fat agents** -- Orchestrator handles routing and coordination only. Domain logic lives in the agents.
- **Fail-partial, not fail-all** -- A report is always produced. Synthesis explicitly reasons over what succeeded and what didn't.
- **No hardcoded agent URLs** -- all resolution goes through the Registry. New agents register themselves and become immediately available.
- **No ADK** -- Google ADK is a full agent framework tied to the Google ecosystem. It hides the protocol mechanics this project exists to learn. ~80 lines of FastAPI replaces it for both inbound and outbound A2A.
- **SSE for live progress** -- the pipeline takes 10-30s across multiple agents. SSE pushes milestone events in real time instead of making the client wait blind. Events are persisted to SQLite for late-connector replay and audit.
- **CLI over web UI** -- typer + rich provides a full demo experience (`run`, `agents`, `history`, `inspect`) without frontend complexity. SSE events render as a live terminal timeline.

## UC-5: Mortgage Platform (External A2A Caller)

The Mortgage Platform is an independent service that calls the Orchestrator via the standard A2A protocol -- the same way any external system would. It has **zero imports** from `agents/shared/`; it only knows the wire protocol.

```
Mortgage Platform (:9000)
    │  GET /.well-known/agent.json   (discover)
    │  POST /tasks/send              (submit)
    │  GET /tasks/{id}               (poll)
    │  GET /tasks/{id}/stream        (SSE)
Orchestrator (:8000)
    │  ... same pipeline as POST /verify ...
```

### Running UC-5

```bash
# Start all services INCLUDING mortgage-platform
docker compose --profile uc5 up --build -d

# Via CLI
verifyiq run mortgage-platform

# Via curl (trigger the Mortgage Platform directly)
curl -X POST http://localhost:9000/trigger

# With a custom subject
curl -X POST http://localhost:9000/trigger \
  -H "Content-Type: application/json" \
  -d '{"subject_name":"Jane Doe","subject_id":"SIM-US-2000","use_case":"mortgage","has_foreign_addr":false,"consent":true}'
```

### Testing the A2A Callee Endpoints Directly

The Orchestrator now accepts inbound A2A tasks at `POST /tasks/send` (same path data agents use):

```bash
# Submit a task via A2A protocol
curl -X POST http://localhost:8000/tasks/send \
  -H "Authorization: Bearer $VERIFYIQ_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "ext-001",
    "correlation_id": "corr-001",
    "skill": "verify_subject",
    "input": {
      "subject_name": "David Kim",
      "subject_id": "SIM-KR-2018",
      "use_case": "mortgage",
      "has_foreign_addr": true,
      "consent": true
    },
    "timeout_ms": 60000,
    "attempt": 1
  }'

# Poll for result
curl http://localhost:8000/tasks/ext-001

# Stream SSE events
curl http://localhost:8000/tasks/ext-001/stream
```

## Testing

```bash
# All tests (services must be running)
pytest tests/

# Phase 9 tests only (orchestrator callee + mortgage platform)
pytest tests/test_phase9_uc5.py -v

# Tests 1-6 need: docker compose up -d
# Test 7 needs:   docker compose --profile uc5 up -d
```

Tests use `pytest` + `httpx` async + `respx` (mocks OpenRouter only). The Registry and A2A protocol are tested with real HTTP calls, not mocks.

## Development

Run a single agent outside Docker for faster iteration:

```bash
export AGENT_REGISTRY_URL=http://localhost:8099
uvicorn agents.equifax.main:app --port 8001
```

Trace any request across all containers:

```bash
docker compose logs | grep "<correlation_id>"
```
