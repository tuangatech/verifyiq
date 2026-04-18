# VerifyIQ — Multi-Agent Credit & Employment Verification Platform
### Project Specification v1.7
*A side project for learning Agent-to-Agent (A2A) multi-agent orchestration patterns*

> **v1.7 changes:** Phase 7 frontend replaced with a Python CLI (`cli/` package, typer +
> rich). Next.js, Tailwind, shadcn/ui, and EventSource removed from the stack entirely.
> Section 2 entry-point description updated (CLI replaces browser). Section 3 features
> F-1, F-5, F-6, F-7, F-8 rewritten for CLI. Section 4 tech stack updated. Section 5
> architecture diagram updated. Section 6.1 replaced with CLI component breakdown.
> Section 6.3 Orchestrator gains two new endpoints: `GET /verify/history` and
> `GET /verify/{task_id}/full`. Section 10 Phase 7 tests updated. Section 11 observability
> references updated. Section 12 port map and deployment diagram updated (`:3000` removed).
> Section 13 prerequisites updated (Node.js removed; CLI setup added). Section 14 Phase 7
> goal rewritten.
>
> **v1.6 changes:** Four targeted fixes after UC-5 was added — Section 2 intro clarifies
> two entry-point categories (user-initiated UC-1–4 vs platform-initiated UC-5); Section 8
> orchestration flow notes both entry points share the same internal pipeline; F-5 SSE
> updated to cover both browser and external orchestrator consumers; Phase 8 end-to-end
> tests corrected (UC-3 added, UC-5 tests correctly placed in Phase 9). Build sequence
> consolidated from 10 phases to 8: Phase 0 + Phase 1 merged (setup + skeletons belong
> in one session); Phase 6 + Phase 7 merged (LangGraph + SSE are independent concerns
> that fit naturally in one session). Phase references updated throughout.

---

## Table of Contents

1. [Project Summary](#1-project-summary)
2. [Use Cases & Demo Scenarios](#2-use-cases--demo-scenarios)
   - UC-1a / UC-1b: Mortgage Check (International available / unavailable)
   - UC-2: Rental Screening
   - UC-3: Auto Loan
   - UC-4: International Hire
   - **UC-5: Mortgage Platform calling VerifyIQ (VerifyIQ as A2A server)**
3. [Functional Features](#3-functional-features)
4. [Tech Stack](#4-tech-stack)
5. [Technical Architecture](#5-technical-architecture)
6. [Component Breakdown](#6-component-breakdown)
7. [Data Model](#7-data-model)
8. [Agent Workflow](#8-agent-workflow)
9. [LLM Integration](#9-llm-integration)
10. [Testing](#10-testing)
11. [Monitoring & Observability](#11-monitoring--observability)
12. [Deployment](#12-deployment)
13. [Development Environment Setup](#13-development-environment-setup)
14. [Build Sequence](#14-build-sequence)
15. [Open Questions & Future Considerations](#15-open-questions--future-considerations)
16. [Why A2A — Not Hardwired Workflows, MCP Tools, LangGraph; and ADK's Correct Role](#16-why-a2a--not-hardwired-workflows-mcp-tools-langgraph-and-adks-correct-role)

---

## 1. Project Summary

**VerifyIQ** is a simulated multi-agent verification platform built to learn and practice **Agent-to-Agent (A2A) orchestration patterns**. It models a real-world scenario where a loan officer or financial analyst submits a verification request for a person, and multiple specialized AI agents — each running as a fully independent service — collaborate to produce a unified verification report.

The project deliberately mirrors real credit and employment verification ecosystems:

- **Equifax / TransUnion / Experian** — siloed credit bureaus that own their own data and expose it through contracted APIs
- **Equifax Workforce Solutions (The Work Number)** — a separate employment and income verification service
- **International credit bureaus** — Experian International, Dun & Bradstreet — with partial or region-specific data availability

### Why A2A?

Each credit bureau in the verification domain is a separate organization with its own codebase, team, and deployment pipeline. A2A is designed precisely for this: agents that are owned and operated independently communicate through a standardized HTTP protocol and advertise their capabilities via Agent Cards, with no shared code or shared deployment. This is fundamentally different from a hardwired workflow, MCP tools, or LangGraph — all of which require the participating components to live in the same process or codebase. See Section 16 for the full discussion of why each alternative was ruled out and where they do correctly appear within VerifyIQ.

### Learning Objectives

- Agent Card structure and capability advertisement
- Dynamic agent discovery via a central Agent Registry
- Parallel fan-out: dispatching multiple tasks concurrently and collecting results
- Sequential chaining: passing one agent's outcome as another agent's input
- Task lifecycle management (`submitted → working → completed / failed / timed_out / skipped`)
- Graceful degradation when an optional agent fails, times out, or returns no data
- Correlation IDs flowing end-to-end for cross-service tracing
- Timeout and retry handling in distributed agent coordination
- SSE streaming for real-time progress on long-running tasks
- LLM-powered structured output within individual agents via OpenRouter
- Framework-agnostic agent interop (FastAPI agents alongside a LangGraph agent)
- **ADK as an A2A server** — handling inbound tasks from external orchestrators you don't control, including task lifecycle management, SSE streaming back to the caller, and Agent Card advertisement

---

## 2. Use Cases & Demo Scenarios

Use cases fall into two categories by entry point. **UC-1 through UC-4** are user-initiated: a person runs `verifyiq run <scenario>` from the CLI, which calls `POST /verify` on the Orchestrator. Each exercises a different combination of agents and data paths, making the core A2A patterns — fan-out, optional agent handling, graceful degradation, sequential chaining — visible in isolation. **UC-5** is platform-initiated: an external Mortgage Platform Orchestrator calls VerifyIQ as a registered A2A sub-agent via `POST /tasks/send`. The internal pipeline is identical in both categories; what differs is the entry point and who receives the result.

UC-1 has two explicit variants for the International Agent (data available vs. unavailable), making the graceful degradation pattern directly comparable side-by-side.

---

### UC-1a: Mortgage Check — International Data Available
**Who:** Loan officer reviewing a Vietnamese-American borrower who lived in Vietnam until 2019.

**Subject fields:** Name, simulated SSN, `has_foreign_addr: true`, `country_of_origin: VN`, use case: `mortgage`.

**Agents invoked:** Equifax → Employment → International (all three) → Risk Synthesis.

**International Agent behavior:** Finds foreign address history, generates an `experian_international` artifact with `data_availability: full`, a Vietnamese local score mapped to a US-equivalent score, and `country_risk_tier: moderate`.

**Expected outcome:** Full four-agent report. Risk Synthesis reasons across all three data sources. International score is factored into the decision.

---

### UC-1b: Mortgage Check — International Data Unavailable
**Who:** Loan officer reviewing a borrower born and raised in the US with no foreign addresses.

**Subject fields:** Name, simulated SSN, `has_foreign_addr: true` (officer checked the box, but the bureau finds nothing), use case: `mortgage`.

**Agents invoked:** Equifax → Employment → International → Risk Synthesis.

**International Agent behavior:** Finds no foreign address history. Returns `data_availability: unavailable`. The Orchestrator marks the task `completed` with a null artifact and passes a structured outcome to Risk Synthesis.

**Expected outcome:** Report with international section showing "unavailable". Risk Synthesis explicitly notes it cannot assess foreign credit risk and adjusts confidence accordingly.

**Demo value:** Side-by-side with UC-1a shows graceful degradation — same agent invoked, different data path, Orchestrator does not fail.

---

### UC-2: Apartment Rental Screening
**Who:** Property manager screening a rental applicant.

**Subject fields:** Name, simulated SSN, `has_foreign_addr: false`, use case: `rental`.

**Agents invoked:** Equifax → Employment → Risk Synthesis. International Agent **not invoked** (skipped entirely).

**Demo value:** Orchestrator selectively omits optional agents — distinct from UC-1b where the agent is called and returns unavailable.

---

### UC-3: Auto Loan Verification
**Who:** Auto dealership finance desk verifying a buyer.

**Subject fields:** Name, simulated SSN, `has_foreign_addr: false`, use case: `auto`.

**Agents invoked:** Equifax → Employment → Risk Synthesis.

**Risk Synthesis behavior:** System prompt weights debt-to-income ratio and employment stability more heavily than raw credit score for this use case type.

---

### UC-4: International Hire Background Check
**Who:** HR manager onboarding a new employee who lived abroad.

**Subject fields:** Name, `has_foreign_addr: true`, use case: `hire`.

**Agents invoked:** International → Employment → Risk Synthesis. Equifax **not invoked**.

**Demo value:** Orchestrator invokes only agents relevant to the use case — not all agents run on every request.

---

### UC-5: Mortgage Platform Calls VerifyIQ (VerifyIQ as A2A Server)

**Who:** An external Mortgage Platform Orchestrator — a separate ADK agent built by a different team — that manages the full loan origination pipeline (document collection, appraisal scheduling, underwriting, rate locking). It needs subject verification and calls VerifyIQ as a registered sub-agent.

**Setup:** The Mortgage Platform Orchestrator is a second Docker Compose service (`mortgage-platform`) running an ADK agent. It has its own Registry (or uses VerifyIQ's) and discovers VerifyIQ via VerifyIQ's Agent Card at `http://orchestrator:8000/.well-known/agent.json`. It then dispatches an A2A task to `POST http://orchestrator:8000/tasks/send` with a verification request payload.

**What VerifyIQ's Orchestrator does:** Receives the inbound A2A task via ADK's server-side handler. ADK manages the task lifecycle back to the Mortgage Platform — accepting the task, returning a `task_id` immediately, streaming progress events via SSE, and delivering the final `VerificationDecision` artifact as the task result. VerifyIQ internally runs its standard pipeline (Equifax → Employment → Intl → Risk Synthesis) exactly as it does for UC-1a, then sends the result back to the caller via the A2A protocol.

**The Mortgage Platform never touches VerifyIQ's internal code.** It only knows VerifyIQ's Agent Card (skills it advertises) and the A2A task protocol. VerifyIQ's team can update its internal agents, change its LLM, or restructure its pipeline without the Mortgage Platform team knowing or caring.

```
Mortgage Platform Orchestrator (ADK)    ← external team, separate codebase
    └─ A2A → VerifyIQ Orchestrator (ADK server)
                  ├─ A2A → Equifax Agent
                  ├─ A2A → Employment Agent
                  ├─ A2A → Intl Agent
                  └─ A2A → Risk Synthesis Agent
```

**Key A2A learning:** This is the scenario where ADK earns its place. ADK on the VerifyIQ server side handles: receiving and validating the inbound A2A task, managing the task state machine back to the Mortgage Platform caller, streaming progress via SSE to an external consumer, and delivering the final artifact. Without ADK, these server-side protocol responsibilities would need to be hand-rolled — non-trivial code covering reconnects, streaming backpressure, and task cancellation. This use case makes VerifyIQ a true peer in an A2A ecosystem, not just a demo orchestrator.

**Demo value:** Run both services in Docker Compose. Show the Mortgage Platform dispatching a task, watch VerifyIQ's pipeline execute internally, and observe the decision artifact returned to the Mortgage Platform — all without the Mortgage Platform having any knowledge of Equifax, LangGraph, or OpenRouter. This is the full A2A vision: independent services collaborating through a protocol contract.

### F-1: Verification Request Submission
The user runs `verifyiq run <scenario>`, which maps the scenario name to a pre-defined subject payload (name, simulated subject ID, use case type, foreign address flag, consent). The CLI sends `POST /verify` to the Orchestrator, which assigns a `correlation_id` that flows through the entire pipeline, returns a `task_id` immediately, and begins processing in the background.

### F-2: Parallel Fan-Out to Data Agents
Fan-out means dispatching multiple independent tasks simultaneously and waiting for all of them to complete before proceeding. The Orchestrator resolves agents via the Registry by skill, then fires tasks to all relevant data agents concurrently using `asyncio.gather`. Each task carries the `correlation_id` and a `timeout_ms` field. Results are collected as they arrive.

### F-3: Optional Agent Handling
If the International Agent is not needed, its task is marked `skipped`. If dispatched but returns `data_availability: unavailable`, its task is marked `completed` with a null artifact. If it times out, its task is marked `timed_out`. In all cases the Orchestrator continues without blocking, and the exact outcome reason is passed to Risk Synthesis.

### F-4: Sequential Risk Synthesis
After all data agent tasks resolve, the Orchestrator passes a structured **outcome bundle** to the Risk Synthesis Agent. Each outcome includes the agent name, final status, artifact (if any), and structured error (if failed or timed out). Risk Synthesis reasons over complete evidence including failure context, not just successful outputs.

### F-5: SSE Progress Streaming
SSE (Server-Sent Events) is a one-directional HTTP streaming mechanism where the server pushes events to the client over a persistent connection without the client polling. The Orchestrator emits progress events at each milestone — "Agents resolved", "Credit report received", "Employment verified", "Running risk synthesis" — and every event carries the `correlation_id` for tracing. SSE streams to two different consumer types depending on the entry point: the **CLI** (UC-1 to UC-4, via `verifyiq run --watch`) receives events rendered as a rich live timeline in the terminal; the **Mortgage Platform Orchestrator** (UC-5) receives the same event stream as structured JSON it can log or forward. The mechanism is identical — ADK on the server side manages the streaming connection regardless of which consumer is listening.

### F-6: Structured Verification Report
The CLI renders the final report as rich panels: credit summary, employment summary, international summary (or failure/timeout notice), and the risk decision with decision factors and reasoning. A `--json` flag outputs the raw JSON for scripting.

### F-7: Agent Network Table
`verifyiq agents` lists all agents registered with the Registry — name, URL, skills, health, average latency, last seen — rendered as a rich table.

### F-8: Request History
`verifyiq history` shows past verification requests with subject name, use case, timestamp, decision, and task ID. `verifyiq inspect <task_id>` shows the full artifact dump for a completed request.

### F-9: Task Audit Log
Every A2A task logged to SQLite: agent called, `correlation_id`, skill, input payload, output artifact or error, `started_at`, `ended_at`, status.

### F-10: Dynamic Agent Registration
Any compliant A2A agent that POSTs its Agent Card to the Registry on startup is immediately discoverable. No Orchestrator changes required.

### F-11: Inbound A2A Task Handling (ADK Server-Side)
The Orchestrator accepts A2A tasks from external orchestrators — such as the Mortgage Platform — via `POST /tasks/send`. ADK manages the server-side A2A protocol: task receipt and validation, immediate `task_id` acknowledgement, task lifecycle state machine updates visible to the caller via `GET /tasks/{task_id}`, SSE progress streaming back to the caller, and final artifact delivery. The caller needs no knowledge of VerifyIQ's internal pipeline — it only interacts with the A2A task protocol.

---

## 4. Tech Stack

### Layer Table

| Layer | Technology | Role |
|---|---|---|
| **CLI** | Python + typer + rich | Terminal UI — scenario runner, live SSE view, agent table, history |
| **CLI HTTP** | httpx | Calls Orchestrator REST + SSE endpoints |
| **Orchestrator** | Python + FastAPI + ADK | Hub agent — discovery, fan-out, chaining, SSE emission |
| **Data agents** | Python + FastAPI | Equifax, International, Risk Synthesis agents |
| **Employment agent** | Python + LangGraph | Multi-step stateful verification graph |
| **Agent Registry** | Python + FastAPI | Agent self-registration and skill-based discovery |
| **LLM routing** | OpenRouter | Unified API gateway to multiple LLM providers |
| **Data agent LLM** | `google/gemini-3-flash` | Fast, low-cost structured JSON generation |
| **Synthesis LLM** | `openai/gpt-5.4-mini` | Multi-artifact reasoning and structured decision output |
| **Orchestrator DB** | SQLite (`verifyiq.db`) | Task state, audit log, SSE event history |
| **Registry DB** | SQLite (`registry.db`) | Agent registrations |
| **Containerization** | Docker + Docker Compose | Local multi-service orchestration |
| **Inter-agent protocol** | A2A (HTTP + JSON-RPC + SSE) | Agent-to-agent task dispatch and result collection |
| **Shared schemas** | Python + Pydantic | Artifact and protocol type definitions shared across agents |

### Trade-offs Made

**SQLite instead of PostgreSQL.** Zero-ops for local dev. The Orchestrator is the only writer to `verifyiq.db`; the Registry is the only writer to `registry.db`. No concurrent write contention. Cloud migration path: swap connection string only.

**Plain Python functions instead of real MCP servers inside agents.** Same structural pattern (named, typed, single-responsibility functions) without MCP wire protocol overhead. Keeps focus on A2A as the learning target. See Section 16 for the full reasoning.

**ADK for the Orchestrator's server-side A2A handling.** ADK's primary value in VerifyIQ is on the server side — receiving inbound A2A tasks from external orchestrators (UC-5: Mortgage Platform). It handles task receipt, state machine management back to the caller, SSE streaming to external consumers, and task cancellation. These are non-trivial to hand-roll correctly. ADK also provides Agent Card generation and an A2A client for outbound dispatch, though those are simpler and could be replaced with plain `httpx` if needed. The trade-off is less community documentation than FastAPI alone. If ADK causes friction, the client-side dispatch is ~50 lines of `httpx` and replaceable independently of the server-side handler.

**LangGraph only for the Employment Agent.** Demonstrates framework-agnostic A2A interop without making the whole project LangGraph-heavy. See Section 16 for why LangGraph is not used at the orchestration level.

**No auth in Phases 1–7.** All agents run on the Docker internal network. Phase 8 adds bearer tokens.

**OpenRouter instead of direct provider APIs.** Single key, single endpoint. Model string in env var — swap providers without code changes.

**Single repository.** Appropriate for a side project. Docker container boundaries enforce the service independence that separate repos would enforce in production.

**Global timeout, not per-skill timeouts.** A flat `AGENT_TIMEOUT_SECONDS=30` env var applies to all agent tasks. Per-skill timeout configuration adds setup complexity without proportional learning value at this scale.

---

## 5. Technical Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│         MORTGAGE PLATFORM ORCHESTRATOR  (ADK)  :9000             │
│              external team — separate codebase                   │
│  Calls VerifyIQ as a registered A2A sub-agent (UC-5)            │
└───────────────────────────┬──────────────────────────────────────┘
                            │ A2A: POST /tasks/send  (inbound)
                            │     GET /tasks/{id}/stream (SSE back)
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│              ORCHESTRATOR AGENT  (ADK + FastAPI)  :8000          │
│                                                                  │
│  ADK server-side: receives inbound tasks, manages lifecycle      │
│  back to Mortgage Platform, streams SSE to external caller       │
│                                                                  │
│  ┌──────────────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  AgentResolver       │  │ Task Manager │  │ SSE Streamer  │  │
│  │  (queries Registry   │  │ (SQLite)     │  │               │  │
│  │   by skill)          │  │              │  │               │  │
│  └──────────┬───────────┘  └──────────────┘  └───────────────┘  │
│             │ GET /agents?skill=...  (outbound discovery)        │
└─────────────┼────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│              AGENT REGISTRY  (FastAPI)  :8099                   │
│   POST /register  │  GET /agents?skill=  │  DELETE /agents/{id} │
│                        registry.db                              │
└──────────────────────────┬──────────────────────────────────────┘
          ▲                │
          │ POST /register └──────────────────────────┐
          │ (on each agent startup)                   │
          │                                           │
┌─────────┴────┐  ┌──────────────┐  ┌────────────┐  ┌┴────────────┐
│   EQUIFAX    │  │  EMPLOYMENT  │  │    INTL    │  │    RISK     │
│   AGENT      │  │  AGENT       │  │   AGENT    │  │  SYNTHESIS  │
│  (FastAPI)   │  │ (LangGraph)  │  │ (FastAPI)  │  │   AGENT     │
│   :8001      │  │   :8002      │  │  :8003     │  │ (FastAPI)   │
│              │  │              │  │            │  │  :8004      │
└──────────────┘  └──────────────┘  └────────────┘  └─────────────┘
        │               │                │                │
        └───────────────┴────────────────┴────────────────┘
              All A2A messages carry: correlation_id, attempt,
              started_at / ended_at, timeout_ms
                        │
                 ┌─────────────┐
                 │   SQLite    │
                 │ verifyiq.db │
                 └─────────────┘
```

### Agent Call Graph

```
── Standard user-initiated flow (UC-1 through UC-4) ──────────────────
User (terminal)
 └→ CLI (verifyiq run) → POST /verify → Orchestrator (:8000)
                                    ├→ [queries Registry]
                                    ├→ [parallel] → Equifax Agent (:8001)
                                    ├→ [parallel] → Employment Agent (:8002)
                                    ├→ [parallel, optional] → Intl Agent (:8003)
                                    └→ [sequential] → Risk Synthesis (:8004)

── External platform-initiated flow (UC-5) ────────────────────────────
Mortgage Platform Orchestrator (:9000)    ← external team
 └→ A2A: POST /tasks/send → Orchestrator (:8000)   [ADK server receives]
                                    ├→ [queries Registry]
                                    ├→ [parallel] → Equifax Agent (:8001)
                                    ├→ [parallel] → Employment Agent (:8002)
                                    ├→ [parallel, optional] → Intl Agent (:8003)
                                    └→ [sequential] → Risk Synthesis (:8004)
          ←─ SSE stream of progress events ────────────────────────────┘
          ←─ Final VerificationDecision artifact via A2A task result ──┘
```

In UC-5, the Orchestrator plays **both roles simultaneously**: it is a callee (A2A server, receiving the Mortgage Platform's task via ADK) and a caller (A2A client, dispatching outbound tasks to data agents). The data agents below it see no difference — they receive the same A2A tasks regardless of who initiated the top-level request.

The internal pipeline is a **fixed DAG** (directed acyclic graph) in both flows:

```
           ├──→ Risk Synthesis
Employment─┤
           │
Intl ──────┘ (optional node)
```

Three parallel nodes feed into one sequential node. The DAG is fixed per use case type — which parallel nodes are active varies, but the structure does not. This makes it straightforward to add future agents (e.g. FraudDetection, IncomeVerification) by adding a node to the parallel layer without touching the synthesis chain.

Data agents and the Risk Synthesis Agent are **pure receivers** — they accept tasks and return outcomes. Only the Orchestrator initiates calls.


### Key Architecture Principles

**A2A as the exclusive inter-agent transport.** All communication goes through `POST /tasks/send` and `GET /tasks/{id}`. No shared databases, no direct function imports across agent boundaries.

**Internal tools are plain Python functions, not MCP servers.** Each agent has internal helper functions it calls to build its artifact. These are named, single-responsibility, and typed — the same pattern as MCP tools — but implemented as plain Python without the wire protocol. This keeps focus on A2A as the learning target.

**Thin orchestrator, fat agents.** The Orchestrator handles discovery, dispatch, collection, and chaining — no domain logic. Each agent owns its domain logic, LLM prompting, and internal tool calls.

**Registry as infrastructure.** The Agent Registry is established in Phase 2, before any task protocol or domain logic is built. Every subsequent phase is registry-aware from day one.

**Fail-partial, not fail-all.** The Orchestrator produces a report even when agents fail, time out, or return no data. Risk Synthesis receives structured outcome objects — not just artifacts — so it can reason explicitly over what succeeded, what failed, and why.

**The organizational boundary determines the framework.** The choice between A2A, MCP tools, and LangGraph is not a technical preference — it follows a single question: *does this cross a team or deployment boundary?* Across organizational boundaries (different teams, independent deployments) → A2A. Within one agent's internal logic, with branching state → LangGraph. Within one agent's simple operations → plain Python functions. This rule is applied consistently throughout VerifyIQ and generalizes to any multi-agent system design. See Section 16 for the full discussion.

**Correlation IDs flow end-to-end.** Every A2A task carries a `correlation_id` generated at request intake. It flows from the Orchestrator into every agent task, into every SSE event, and into every SQLite log row. A single `grep correlation_id <value>` across all container logs reconstructs the complete trace of any verification request without a distributed tracing system.

---

## 6. Component Breakdown

### 6.1 CLI — Python Package (`cli/`)

A standalone Python package (`verifyiq-cli`) using typer + rich. Installed with `uv pip install -e cli/` or run directly via `uv run --project cli verifyiq`. Communicates with the Orchestrator over HTTP only — no path dependency on `agents/shared/`.

**Commands:**

| Command | Description | Orchestrator endpoint |
|---|---|---|
| `verifyiq run <scenario> [--no-watch]` | Run a scenario; stream SSE events by default | `POST /verify` + `GET /verify/{task_id}/stream` |
| `verifyiq agents` | List registered agents as a rich table | `GET /agents` |
| `verifyiq history [--limit N]` | Show past verification requests | `GET /verify/history` |
| `verifyiq inspect <task_id>` | Full artifact dump for a completed request | `GET /verify/{task_id}/full` |

**Scenarios** (mapped to pre-defined subject payloads in `scenarios.py`):

| Scenario name | Use case | Notes |
|---|---|---|
| `mortgage-intl` | UC-1a | International data available |
| `mortgage-domestic` | UC-1b | International data unavailable |
| `rental` | UC-2 | International skipped |
| `auto` | UC-3 | DTI-weighted synthesis |
| `hire` | UC-4 | Equifax skipped |
| `mortgage-platform` | UC-5 | Requires Mortgage Platform service (Phase 9) |

**Package structure:**
```
cli/
  pyproject.toml          # deps: typer, rich, httpx, pydantic
  verifyiq_cli/
    __init__.py
    main.py               # typer app entry point
    commands/
      run.py              # verifyiq run
      agents.py           # verifyiq agents
      history.py          # verifyiq history
      inspect.py          # verifyiq inspect
    client.py             # httpx wrapper; VERIFYIQ_URL env var (default http://localhost:8000)
    models.py             # lightweight response models
    scenarios.py          # scenario name → request payload mapping
    display.py            # rich tables, panels, SSE event rendering
```

**Configuration:** `VERIFYIQ_URL` env var (default `http://localhost:8000`). No other config required.

---

### 6.2 Agent Registry — FastAPI (:8099)

Lightweight standalone service (~120 lines). Infrastructure that agents register with and the Orchestrator queries. Not itself an A2A agent.

**Why separate from Orchestrator:** Avoids circular dependency — agents must register before the Orchestrator can route to them; the Registry must be up before agents start.

**Endpoints:** `POST /register`, `GET /agents?skill=`, `DELETE /agents/{url_hash}`, `GET /health`

**State:** `registry.db` on a separate Docker volume.

---

### 6.3 Orchestrator Agent — ADK + FastAPI (:8000)

The Orchestrator is the system's hub. It plays two distinct roles depending on who initiates the interaction:

- **Caller role (UC-1 to UC-4):** Receives a request from the CLI via `POST /verify`, resolves agents from the Registry, fans out tasks to data agents, chains to Risk Synthesis, and streams progress back to the CLI via SSE.
- **Callee role (UC-5):** Receives an inbound A2A task from the Mortgage Platform Orchestrator via `POST /tasks/send`. ADK handles the server-side protocol — task receipt, state machine management, SSE streaming back to the external caller, and final artifact delivery. Internally the same pipeline runs as in UC-1 to UC-4.

**ADK's role here is specifically the callee path.** When VerifyIQ is called by an external orchestrator it does not control, ADK manages the A2A server protocol so the team can focus on the pipeline logic. The outbound dispatch to data agents uses plain `httpx` wrapped in `TaskDispatcher`.

**Endpoints:**

| Endpoint | Method | Description |
|---|---|---|
| `/.well-known/agent.json` | GET | Orchestrator's Agent Card — discovered by Mortgage Platform |
| `POST /verify` | POST | User-initiated: accepts a verification request, returns `task_id` |
| `GET /verify/{task_id}/stream` | GET | SSE stream for CLI and external callers |
| `GET /verify/{task_id}` | GET | Poll for task status (CLI or external caller) |
| `POST /tasks/send` | POST | **A2A server endpoint** — receives inbound tasks from external orchestrators (ADK-handled) |
| `GET /tasks/{task_id}` | GET | A2A task status — polled by external orchestrators |
| `GET /verify/history` | GET | Past verification requests — subject, use case, timestamp, decision |
| `GET /verify/{task_id}/full` | GET | Full artifact dump — all agent outcomes + final decision |
| `GET /agents` | GET | Proxies Registry contents for the CLI agent table |
| `GET /health` | GET | Liveness check |

**Internal components:**

`AgentResolver` — Queries Registry by skill. Selects agent with lowest average latency among healthy candidates. No agent URLs hardcoded.

`TaskDispatcher` — Sends outbound A2A tasks with `correlation_id`, `timeout_ms`, and `attempt`. One retry on HTTP 5xx or connection error with 1-second delay. Returns structured `AgentError` on second failure.

`TaskManager` — Manages `verification_requests` and `agent_tasks` rows in SQLite. Records `started_at`, `ended_at`, and terminal status for every task.

`SSEStreamer` — Publishes progress events to connected clients (CLI or external orchestrator) with `correlation_id` on every event.

**Internal tools (plain Python functions):**

| Tool | Description |
|---|---|
| `resolve_agents(skills)` | Batch-queries Registry for a list of skills, returns URL map |
| `build_task(skill, input, correlation_id, attempt)` | Constructs `A2ATask` with all required fields |
| `dispatch_with_timeout(agent_url, task)` | Sends task, enforces `AGENT_TIMEOUT_SECONDS`, returns `AgentOutcome` |
| `fan_out(tasks)` | `asyncio.gather` across all parallel tasks, collects `AgentOutcome` list |
| `build_synthesis_payload(outcomes)` | Packages full outcome list for Risk Synthesis Agent |

---

### 6.4 Equifax Agent — FastAPI (:8001)

**Simulates:** Equifax U.S. Information Solutions.

**Startup:** `POST /register` to Registry. `DELETE` on graceful shutdown.

**Agent Card skills:** `credit_score`, `tradeline_summary`, `derogatory_marks`, `inquiry_history`

**LLM:** `google/gemini-3-flash`

**Internal tools:**

| Tool | Description |
|---|---|
| `build_persona_seed(subject)` | Extracts persona hints from request |
| `call_llm_credit_profile(seed)` | Sends structured prompt to OpenRouter, returns raw JSON string |
| `validate_credit_artifact(raw)` | Validates against `EquifaxArtifact` Pydantic model |
| `retry_with_correction(raw, error)` | Re-prompts LLM with validation error, one retry only |

**Output artifact:** credit score, score model, open accounts, utilization %, derogatory marks, hard inquiries (12mo), oldest account age.

---

### 6.5 Employment Verification Agent — LangGraph (:8002)

**Simulates:** Equifax Workforce Solutions / The Work Number.

**Why LangGraph:** Employment verification has genuine branching — check current employer, fall back to prior if not found. LangGraph's stateful graph handles this naturally. It also demonstrates framework-agnostic A2A interop — the Orchestrator sees only the task protocol; LangGraph is an internal implementation detail.

**Startup:** Same self-registration pattern.

**Agent Card skills:** `employment_status`, `income_verification`, `employment_history`

**LLM:** `google/gemini-3-flash`

**LangGraph nodes (internal tools):**

| Node / Tool | Description |
|---|---|
| `lookup_current_employer` | Checks if subject is currently employed; LLM generates employer record |
| `lookup_prior_employer` | Fallback if not currently employed |
| `verify_income` | Generates verified annual income from "payroll data" |
| `calculate_tenure` | Computes years of service from start date |
| `build_artifact` | Assembles final `EmploymentArtifact` from graph state |

**Output artifact:** employment status, employer name, start date, tenure, employment type, verified income, confidence level.

---

### 6.6 Experian International Agent — FastAPI (:8003)

**Simulates:** Experian International credit bureau.

**Key behavior:** Returns `data_availability: unavailable` when no foreign address history exists. The Orchestrator continues — the task status is `completed` with a null artifact and the reason is passed to Risk Synthesis.

**Startup:** Same self-registration pattern.

**Agent Card skills:** `international_credit_score`, `country_risk_profile`, `foreign_tradelines`

**LLM:** `google/gemini-3-flash`

**Internal tools:**

| Tool | Description |
|---|---|
| `check_foreign_address_history(subject)` | Determines if international data is available |
| `build_intl_persona_seed(subject)` | Builds country + risk context for the LLM prompt |
| `call_llm_intl_profile(seed)` | Generates international credit profile via LLM |
| `validate_intl_artifact(raw)` | Validates against `InternationalArtifact` Pydantic model |

**Output artifact (available):** country of record, local score, US-equivalent score, foreign tradelines, country risk tier.

**Output artifact (unavailable):** `data_availability: unavailable` + reason string.

---

### 6.7 Risk Synthesis Agent — FastAPI (:8004)

**The sequential chaining anchor.** Receives a structured **outcome bundle** from the Orchestrator — one `AgentOutcome` per data agent, each including status, artifact (if completed), and error (if failed or timed out). Has no subject database access.

**Why outcome objects matter:** The LLM prompt for Risk Synthesis explicitly receives failure context — not just "international data unavailable" but whether that's because the agent returned no data, failed with an error, or timed out. These warrant meaningfully different confidence statements in the decision.

**Startup:** Same self-registration pattern, skill: `risk_synthesis`.

**LLM:** `openai/gpt-5.4-mini`, temperature 0.2.

**Internal tools:**

| Tool | Description |
|---|---|
| `parse_outcome_bundle(payload)` | Extracts and validates each `AgentOutcome` from the input |
| `build_synthesis_prompt(outcomes, use_case)` | Constructs system + user prompt including failure context |
| `call_llm_decision(prompt)` | Sends to OpenRouter, returns raw JSON decision string |
| `validate_decision_artifact(raw)` | Validates against `VerificationDecision` Pydantic model |

**Output artifact:** decision (approve/review/decline), confidence, risk score, decision factors, risk flags, international note, reasoning summary.

---

## 7. Data Model

All state stored in SQLite across two independent databases.

---

### Orchestrator DB — `verifyiq.db`

**`verification_requests`** — One row per verification request. Tracks subject identity, use case, overall lifecycle status, final decision, and timestamps. The `id` is also the `task_id` returned to the UI. Includes `correlation_id` for end-to-end tracing.

**`agent_tasks`** — One row per A2A task dispatched to a remote agent. Records the full input payload, output artifact or structured error, `started_at`, `ended_at`, terminal status, `attempt` number, and `correlation_id`. This is the primary audit trail. A single verification request produces 3–5 rows depending on which agents were invoked.

**`sse_events`** — One row per SSE event emitted to clients. Stores event type (progress / artifact / completed / error), full JSON payload, and `correlation_id`. Enables replaying the exact sequence of events any client received for any historical request.

> `verifyiq.db` is an **observability and client-status store only** — it is never read or written by data agents. All inter-agent communication happens over HTTP (`POST /tasks/send`). The Orchestrator writes to the DB *after* each HTTP response returns, as an audit record of what happened.

---

### Registry DB — `registry.db`

**`registered_agents`** — One row per registered agent. Stores the full Agent Card JSON, skills as a JSON array, health status, average latency, and registration / last-seen timestamps. Primary key is a hash of the agent URL.

> Two separate SQLite files on two separate Docker volumes. The Registry must remain queryable if the Orchestrator restarts; the Orchestrator must write task state if the Registry is briefly down. Separate files enforce this independence.

---

### Data Agent Task Store (in-memory)

Each data agent (Equifax, Employment, Intl, Synthesis) maintains a module-level `task_store: dict[str, dict]` in memory. When `POST /tasks/send` is called, the agent writes the completed `A2ATaskResult` into this dict keyed by `task_id`; `GET /tasks/{task_id}` reads from it. This is not a database — it is a transient in-process cache that does not survive restarts. Its only purpose is to satisfy the A2A polling contract (`GET /tasks/{task_id}`). Durable task history lives in `agent_tasks` in `verifyiq.db`, written by the Orchestrator after each dispatch.

---

## 8. Agent Workflow

### A2A Type Definitions (Key Schemas)

Before the workflow, the key types that flow between agents:

```python
# A2ATask — sent by Orchestrator to a remote agent
class A2ATask(BaseModel):
    task_id: str
    correlation_id: str          # flows end-to-end; same for all tasks in one request
    skill: str
    input: dict[str, Any]
    timeout_ms: int = 30000      # global default; agent should honour if possible
    attempt: int = 1             # 1 = first attempt, 2 = one retry

# A2ATaskResult — returned by a remote agent
class A2ATaskResult(BaseModel):
    task_id: str
    correlation_id: str
    status: Literal["completed", "failed", "timed_out"]
    artifact: dict[str, Any] | None = None
    error: AgentError | None = None   # populated on failed or timed_out
    started_at: str                   # ISO datetime, when agent began processing
    ended_at: str                     # ISO datetime, when agent finished

# AgentError — structured error on failed / timed_out tasks
class AgentError(BaseModel):
    code: str          # e.g. "LLM_VALIDATION_FAILED", "TIMEOUT", "UPSTREAM_ERROR"
    message: str       # human-readable description
    retryable: bool    # true for transient errors (5xx, timeout), false for logic errors

# AgentOutcome — what the Orchestrator passes to Risk Synthesis per data agent
class AgentOutcome(BaseModel):
    agent_name: str
    skill: str
    status: Literal["completed", "failed", "timed_out", "skipped"]
    artifact: dict[str, Any] | None = None
    error: AgentError | None = None
```

---

### A2A Task Lifecycle States

```
submitted → working → completed
                   ↘ failed      (agent returned an error, or retry exhausted)
                   ↘ timed_out   (agent did not respond within timeout_ms)
           ↘ skipped             (agent not invoked for this use case)
```

`timed_out` is a distinct terminal state from `failed`. Risk Synthesis treats them differently in its prompt — a timeout suggests a transient infrastructure issue, while a failure may indicate a data or logic problem.

---

### Orchestration Flow

> **Both entry points — user-initiated (`POST /verify`) and platform-initiated (`POST /tasks/send` from the Mortgage Platform) — run the same internal pipeline.** The entry point determines who receives the `task_id` and SSE stream; everything from `AgentResolver` onward is identical. The diagram below shows the internal pipeline; refer to the Agent Call Graph in Section 5 for the two entry-point flows.

```
User submits verification request
        │
        ▼
[Orchestrator] POST /verify
  → Generate correlation_id (UUID)
  → Write verification_requests row (status: submitted, correlation_id)
  → Return { task_id, correlation_id, stream_url } immediately
        │
        ▼ (background coroutine)
[Orchestrator] AgentResolver queries Registry by skill
  → Determine which agents are needed based on use_case + has_foreign_addr
  → Emit SSE: { type: "progress", message: "Agents resolved", correlation_id }
        │
        ▼
[Orchestrator] Parallel fan-out via asyncio.gather
  — All tasks dispatched simultaneously; collected as they arrive —
  Each task carries: { correlation_id, timeout_ms: 30000, attempt: 1 }

  TaskDispatcher sends to each agent:
    attempt 1 → on HTTP 5xx or connection error:
      wait 1s → attempt 2 → on failure: return AgentError(retryable=True)
    on timeout: return AgentError(code="TIMEOUT", retryable=True)
        │                    │                    │
        ▼                    ▼                    ▼
  [Equifax Agent]     [Employment Agent]   [Intl Agent]
  Records started_at  LangGraph graph      Returns artifact
  Runs LLM            Records started_at   OR unavailable
  Records ended_at    Runs nodes           Records started_at/ended_at
  Returns artifact    Records ended_at
  or AgentError       Returns artifact
                      or AgentError
        │                    │                    │
        └────────────────────┴────────────────────┘
                             │
        [Orchestrator collects AgentOutcome per agent]
        → Write agent_tasks rows (started_at, ended_at, status, correlation_id)
        → Emit SSE progress event per outcome received
                             │
                             ▼
[Orchestrator] Sequential chain to Risk Synthesis
  Builds outcome bundle:
  [
    { agent: "equifax",    status: "completed", artifact: {...}, error: null },
    { agent: "employment", status: "completed", artifact: {...}, error: null },
    { agent: "intl",       status: "timed_out", artifact: null,
      error: { code: "TIMEOUT", message: "...", retryable: true } }
  ]
  POST synthesis_url/tasks/send  (also carries correlation_id)
        │
        ▼
[Risk Synthesis Agent]
  Receives full outcome bundle including failure context
  LLM prompt includes: what each agent returned AND why any failed
  Returns VerificationDecision
        │
        ▼
[Orchestrator]
  → Write decision + completed_at to verification_requests
  → Write synthesis agent_task row
  → Emit SSE: { type: "completed", report: full_report, correlation_id }
  → Close SSE stream
```

---

## 9. LLM Integration

### Model Assignments

| Agent | Model | Reason |
|---|---|---|
| Equifax Agent | `google/gemini-3-flash` | Fast, low cost, strong structured JSON for agentic workflows |
| Employment Agent | `google/gemini-3-flash` | Low latency benefits multi-step LangGraph nodes |
| International Agent | `google/gemini-3-flash` | Same |
| Risk Synthesis Agent | `openai/gpt-5.4-mini` | Multi-artifact reasoning; 400K context fits full outcome bundle |

All model strings are env vars (`OPENROUTER_AGENT_MODEL`, `OPENROUTER_SYNTHESIS_MODEL`). Swapping models requires only an env var change.

### Prompt Strategy — Data Agents

System prompt defines the agent's persona and output schema. User prompt provides the subject persona seed. LLM returns raw JSON only. Output validated against Pydantic model. On failure, one retry with validation error appended to the prompt.

### Prompt Strategy — Risk Synthesis Agent

System prompt instructs the model to act as a senior credit risk analyst. User prompt contains the full `AgentOutcome` bundle. The prompt explicitly distinguishes outcome statuses:

```
Agent outcomes:
- equifax: COMPLETED — credit score 724, utilization 28%, no derogatory marks
- employment: COMPLETED — employed at Acme Corp, income $87,000, tenure 5 years
- intl: TIMED_OUT — international credit bureau did not respond within timeout

Note: The international timeout is likely transient infrastructure. You cannot
assess foreign credit risk. Reduce confidence accordingly.
```

Temperature 0.2. Prompt varies by use case type — `auto` weights debt-to-income more; `mortgage` weighs all sources equally.

### Structured Output Enforcement

All LLM calls use `response_format: { type: "json_object" }` where supported. Output validated against Pydantic model. One retry on validation failure. On second failure the agent returns `AgentError(code="LLM_VALIDATION_FAILED", retryable=False)`.

---

## 10. Testing

Testing focuses on A2A protocol mechanics and orchestration patterns — the parts most likely to break silently.

**Test as you build each phase.** Each phase has a "goal" — tests verify that goal before moving on.

---

### Agent Registry Tests (Phase 2)

- `POST /register` → agent appears in `GET /agents`
- `GET /agents?skill=credit_score` → returns only Equifax Agent
- `GET /agents?skill=nonexistent` → returns `[]`, not an error
- `DELETE /agents/{url_hash}` → agent removed from list
- Duplicate registration (same URL) → upserts, no duplicate row
- Registry persists across simulated restart (SQLite volume survives)

---

### A2A Task Protocol Tests (Phase 3)

- `POST /tasks/send` → response includes `task_id`, `correlation_id`, `started_at`
- `GET /tasks/{task_id}` after completion → status `completed`, `ended_at` populated
- `GET /tasks/{unknown_id}` → 404, not 500
- `correlation_id` in task request appears in task result unchanged
- Task status transitions written to `agent_tasks` with `started_at`, `ended_at`, `correlation_id`
- Orchestrator resolves skill via Registry and sends task with correct fields (integration test)

---

### Orchestration Pattern Tests (Phase 5)

*Most critical tests in the project.*

- **Fan-out concurrency:** Total wall-clock time < sum of individual agent times
- **Optional agent — skipped:** `has_foreign_addr: false` → Intl `agent_tasks` row has status `skipped`
- **Optional agent — unavailable:** Intl returns `unavailable` → task `completed`, null artifact, Orchestrator does not fail
- **Timeout handling:** Intl agent takes > 30s → task status `timed_out`, `AgentError(code="TIMEOUT")` in outcome bundle
- **Retry on 5xx:** Agent returns HTTP 500 → Orchestrator retries once (attempt 2 logged), on second failure task status `failed`
- **Sequential chain:** Risk Synthesis receives full `AgentOutcome` list including any failures
- **Failure context passthrough:** A `timed_out` outcome appears in synthesis input with error details
- **Required agent fails:** Equifax returns HTTP 500, both retries fail → overall request status `failed`
- **correlation_id continuity:** Same `correlation_id` in verification_request row, all agent_task rows, all sse_events rows

---

### SSE Streaming Tests (Phase 6)

- Stream emits at least one `progress` event before first artifact arrives
- Each SSE event contains `correlation_id` matching the request
- Stream emits `completed` as the final event
- All SSE events written to `sse_events` table in order with `correlation_id`
- Client disconnect mid-stream does not crash the Orchestrator

---

### End-to-End Flow Tests (Phase 7)

- `verifyiq run mortgage-intl` (UC-1a): all four agents invoked, international data in report, decision returned
- `verifyiq run mortgage-domestic` (UC-1b): international `unavailable`, report notes it, decision still returned
- `verifyiq run rental` (UC-2): only Equifax + Employment + Synthesis invoked; International task row has status `skipped`
- `verifyiq run auto` (UC-3): only Equifax + Employment + Synthesis invoked; Risk Synthesis prompt weighted toward debt-to-income ratio
- `verifyiq run hire` (UC-4): only International + Employment + Synthesis invoked; Equifax task row has status `skipped`
- `verifyiq agents`: renders table with all registered agents
- `verifyiq history`: shows past requests; `verifyiq inspect <task_id>` shows full artifact dump
- All `correlation_id` values consistent across all SQLite rows for each request
- `verifyiq run mortgage-platform` (UC-5): exits with "not yet implemented" until Phase 9

---

### Test Tooling

| Tool | Use |
|---|---|
| `pytest` | All Python unit and integration tests |
| `httpx` (async) | Test FastAPI endpoints |
| `pytest-asyncio` | Async test support |
| `respx` | Mock OpenRouter calls in unit tests |

---

## 11. Monitoring & Observability

**SQLite audit log (`agent_tasks`):** Full input/output/error/`started_at`/`ended_at`/`correlation_id` per task. Queryable via `verifyiq inspect <task_id>`, `verifyiq history`, or `sqlite3` CLI.

**Structured JSON logging (`structlog`):** Each service logs JSON per event. Key fields: `timestamp`, `agent`, `event`, `task_id`, `correlation_id`, `duration_ms`, `status`. Because `correlation_id` appears in every log line, a single grep reconstructs the complete trace of any request across all 6 containers:

```bash
$ docker compose logs | grep "corr-id-abc123"
```

**SSE event log (`sse_events`):** Every event emitted to clients persisted with `correlation_id`. Enables replay of the exact event sequence for any historical request.

**Agent Network table:** `verifyiq agents` shows Registry state on demand. Health, latency, last seen.

**Deliberately excluded:** Prometheus/Grafana, Jaeger/Zipkin, ELK. `docker compose logs` + `correlation_id` grep is sufficient for a local side project.

---

## 12. Deployment

### Deployment Target

Local only — all services run on a single developer machine via Docker Desktop and Docker Compose.

### Local Deployment Diagram

```
╔══════════════════════════════════════════════════════════════════╗
║  Developer Machine (macOS / Windows / Linux)                     ║
║                                                                  ║
║  ┌──────────────────┐                                            ║
║  │  CLI (host)      │  verifyiq run / agents / history / inspect ║
║  │  VERIFYIQ_URL    │                                            ║
║  └──────┬───────────┘                                            ║
║         │ HTTP/SSE                                               ║
║  ┌──────┼─────────────────────────────────────────────────────┐  ║
║  │  Docker Desktop                                            │  ║
║  │      │                                                     │  ║
║  │      │  ┌──────────────────────────────────────────────┐   │  ║
║  │      │  │  Docker internal network (verifyiq_default)  │   │  ║
║  │      ▼  │                                              │   │  ║
║  │  ┌────────────┐  :8000                                 │   │  ║
║  │  │orchestrator│◄───────────────────────────────┐       │   │  ║
║  │  └─────┬──────┘                                │       │   │  ║
║  │        │ GET /agents?skill=                    │       │   │  ║
║  │        ▼                                       │       │   │  ║
║  │  ┌────────────┐  :8099                         │       │   │  ║
║  │  │  registry  │  registry.db (vol)             │       │   │  ║
║  │  └────────────┘                                │       │   │  ║
║  │        ▲ POST /register                        │       │   │  ║
║  │        │                                       │       │   │  ║
║  │  ┌─────┴──────────────────┐                    │       │   │  ║
║  │  │  :8001  :8002  :8003   │                    │       │   │  ║
║  │  │  equifax employment intl│────────────────────┘       │   │  ║
║  │  │        :8004 synthesis │                            │   │  ║
║  │  └────────────────────────┘                            │   │  ║
║  │            verifyiq.db (vol)                           │   │  ║
║  │  └─────────────────────────────────────────────────────┘   │  ║
║  └────────────────────────────────────────────────────────────┘  ║
║                                                                  ║
║  .env (host)  ──►  docker compose  ──►  env vars in containers   ║
╚══════════════════════════════════════════════════════════════════╝
                              │
                              │ HTTPS
                              ▼
                    ┌──────────────────┐
                    │   OpenRouter     │
                    │  gemini-3-flash  │
                    │  gpt-5.4-mini    │
                    └──────────────────┘
```

### Port Map

| Service | Port | Notes |
|---|---|---|
| Orchestrator | 8000 | Hub — serves CLI and external A2A callers |
| Agent Registry | 8099 | |
| Equifax Agent | 8001 | |
| Employment Agent | 8002 | |
| International Agent | 8003 | |
| Risk Synthesis Agent | 8004 | |
| **Mortgage Platform Orchestrator** | **9000** | **UC-5 only — simulated external caller** |

### Docker Volumes

| Volume | Contents |
|---|---|
| `sqlite_data` | `verifyiq.db` (orchestrator) |
| `registry_data` | `registry.db` (registry) |

### Cloud Deployment Path (Future)

`verifyiq.db` + `registry.db` → Neon PostgreSQL, Docker Compose → Railway. Only connection strings change — no code changes.

---

## 13. Development Environment Setup

### Prerequisites

- **Docker Desktop** — runs all agent containers
- **Python 3.11+** — all agents + CLI
- **`uv`** — Python package manager
- **SQLite CLI** — inspect databases during development
- **OpenRouter account** — API key + small credit balance
- **Git** — single repository

### CLI Setup

```bash
uv pip install -e cli/        # installs verifyiq-cli with typer, rich, httpx
export VERIFYIQ_URL=http://localhost:8000   # default; optional
verifyiq run mortgage-intl     # test run
```

### Environment Variables (`.env`)

One `.env` at the repository root, git-ignored. `.env.example` committed.

Key variables:
- `OPENROUTER_API_KEY` — single key for all LLM calls
- `OPENROUTER_AGENT_MODEL` — default: `google/gemini-3-flash`
- `OPENROUTER_SYNTHESIS_MODEL` — default: `openai/gpt-5.4-mini`
- `AGENT_REGISTRY_URL` — default: `http://registry:8099`
- `AGENT_TIMEOUT_SECONDS` — default: `30` (global timeout for all agent tasks)

### Per-Agent `requirements.txt`

Each agent has its own `requirements.txt`. Common dependencies (FastAPI, uvicorn, httpx, pydantic) are repeated intentionally — preserves agent independence.

### Dockerfiles

Each agent: `python:3.11-slim` base, copies `agents/shared/` and the agent directory, installs `requirements.txt`, exposes its port. The CLI runs on the host (not containerized).

### Running One Agent Outside Docker

Set `AGENT_REGISTRY_URL=http://localhost:8099` and run `uvicorn main:app --port 800X` from the agent directory. The rest of the stack remains in Docker. Useful for rapid iteration on a single agent.

---

## 14. Build Sequence

Each phase produces something runnable. Tests for each phase are written before moving to the next. The Registry is established in Phase 2 so all subsequent phases are registry-aware from day one.

---

### Phase 1 — Setup, Skeletons & Agent Cards (3–5 hours)

**Why merged from two:** Prerequisites and skeleton services belong in the same session — tool installation alone produces no runnable output, and standing up containers is the natural first verification step.

Install Docker Desktop, Python 3.11+, `uv`, SQLite CLI. Create OpenRouter account and verify API key with a live `curl` test. Initialize the repository, `.gitignore`, `.env.example`. Create `agents/shared/schemas.py` and `agents/shared/a2a_types.py` with all Pydantic types (`A2ATask`, `A2ATaskResult`, `AgentError`, `AgentOutcome`, `EquifaxArtifact`, `EmploymentArtifact`, `InternationalArtifact`, `VerificationDecision`).

Stand up all 6 services (Registry + 4 agents + Orchestrator) as FastAPI stubs. Each serves `/.well-known/agent.json` and `GET /health`. Write `docker-compose.yml` with all services, health checks, and `AGENT_TIMEOUT_SECONDS` env var. No database, no LLM, no task protocol yet.

**Goal:** All tools verified. All containers start and respond. Agent Cards readable — you understand the contract each agent advertises before writing any protocol code.

---

### Phase 2 — Agent Registry (2–3 hours)

Build the Registry service (~120 lines): `POST /register`, `GET /agents?skill=`, `DELETE /agents/{url_hash}`, SQLite persistence for `registry.db`. Add a FastAPI `lifespan` hook to each agent that POSTs its Agent Card to the Registry on startup and sends `DELETE` on graceful shutdown. Add `AgentResolver` to the Orchestrator — queries Registry by skill, returns the healthiest lowest-latency agent URL. Update `GET /orchestrator/agents` to proxy the Registry.

**Tests:** All Agent Registry tests from Section 10.

**Goal:** All agents self-register. Skill-based discovery works. Registry persists across restarts. `curl "http://localhost:8099/agents?skill=credit_score"` returns exactly the Equifax Agent.

---

### Phase 3 — A2A Task Protocol (3–4 hours)

Implement `POST /tasks/send` and `GET /tasks/{task_id}` on all four remote agents. Tasks carry `correlation_id`, `attempt`, `timeout_ms`. Results carry `started_at`, `ended_at`, `correlation_id`, and a hardcoded stub artifact. Create `verifyiq.db` schema (`verification_requests`, `agent_tasks`, `sse_events`). Implement `TaskManager`. Wire `POST /verify` end-to-end: write a `verification_requests` row, resolve agents via Registry, dispatch a test task to the Equifax Agent, write the result to `agent_tasks`, return `task_id`.

Stubs are intentional — hardcoded artifacts let you verify the full protocol round-trip (Registry → dispatch → collect → SQLite) before introducing LLM complexity. If something breaks in Phase 4, you will know it is the LLM, not the protocol.

**Tests:** All A2A Task Protocol tests from Section 10.

**Goal:** Full A2A task round-trip with all protocol fields. `correlation_id` visible in SQLite rows. Protocol verified clean before LLMs are added.

---

### Phase 4 — LLM Artifact Generation (2–3 hours)

Replace hardcoded stub artifacts in the Equifax, International, and Employment agents (single-node stub for Employment — LangGraph comes in Phase 5) with real LLM calls via OpenRouter. Implement each agent's internal tool functions (`build_persona_seed`, `call_llm_*`, `validate_*_artifact`, `retry_with_correction`). Add Pydantic output validation and one LLM retry on validation failure — distinct from the network retry in `TaskDispatcher`.

**Tests:** Call each agent's `POST /tasks/send` directly. Validate artifact against Pydantic schema. Run three calls and verify output varies.

**Goal:** Each data agent produces a realistic, schema-valid, varied artifact on every call. Prompt engineering for structured JSON output confirmed working.

---

### Phase 5 — Orchestration Patterns (3–4 hours)

Implement the full orchestration flow: parallel fan-out via `asyncio.gather` across Equifax + Employment + Intl; `TaskDispatcher` with `AGENT_TIMEOUT_SECONDS` enforcement and one network retry (attempt 1 → wait 1s → attempt 2 → `AgentError`); `timed_out` terminal state; optional agent skip/unavailable logic; sequential chain to Risk Synthesis with full `AgentOutcome` bundle. Write all `agent_tasks` rows with `started_at`, `ended_at`, `correlation_id`, `attempt`.

**Tests:** All Orchestration Pattern tests from Section 10. Most critical tests in the project.

**Goal:** All four A2A orchestration patterns working end-to-end. `timed_out` and `failed` handled gracefully. Risk Synthesis receives outcome objects, not just artifacts.

---

### Phase 6 — LangGraph Employment Agent + SSE Streaming (4–5 hours)

**Why merged:** These are independent concerns with no shared code dependencies. Both are medium effort, and completing both in one session produces a meaningfully more complete system — real branching agent logic visible in real time.

*LangGraph Employment Agent:* Replace the Employment Agent's single-node stub with a real LangGraph graph: `lookup_current_employer → lookup_prior_employer` (conditional) `→ verify_income → calculate_tenure → build_artifact`. Wire the terminal graph state into `A2ATaskResult`. The Orchestrator sees no difference — same endpoint, same A2A task.

*SSE Streaming:* Implement `GET /verify/{task_id}/stream` as a FastAPI `StreamingResponse`. Emit a progress SSE event at each milestone: agents resolved, each artifact received, synthesis complete, overall completed. Persist every event to `sse_events` with `correlation_id`. The same stream serves both the CLI (UC-1 to UC-4) and the Mortgage Platform (UC-5, Phase 9).

**Tests:** LangGraph branching (unemployed subject triggers `lookup_prior_employer`). All SSE Streaming tests from Section 10.

**Goal:** LangGraph running as a compliant A2A server indistinguishable from FastAPI agents. Live event stream visible via `curl -N`. `correlation_id` traceable across all container logs with a single grep.

---

### Phase 7 — CLI (3–4 hours)

Build the `cli/` Python package (`verifyiq-cli`). Structure: `pyproject.toml`, `verifyiq_cli/` with `main.py` (typer app), `commands/` (run, agents, history, inspect), `client.py` (httpx wrapper), `models.py`, `scenarios.py`, `display.py` (rich rendering). Add `GET /verify/history` and `GET /verify/{task_id}/full` endpoints to the Orchestrator.

- `verifyiq run <scenario> [--no-watch]` — six scenarios mapped to pre-defined subject payloads; SSE streaming by default with rich live display
- `verifyiq agents` — rich table of registered agents
- `verifyiq history [--limit N]` — past verification requests
- `verifyiq inspect <task_id>` — full artifact dump with rich panels
- `verifyiq run mortgage-platform` — exits with "not yet implemented" until Phase 9

**Tests:** All End-to-End Flow tests from Section 10 (UC-1a through UC-4, agents, history, inspect).

**Goal:** Full usable demo tool. No Swagger or `curl` required to run a verification end-to-end.

---

### Phase 8 — Auth & Polish (stretch)

Bearer token verification: agents validate `Authorization: Bearer <token>` on `POST /tasks/send`; Registry validates on `POST /register`. Shared secrets via env vars. Add `structlog` JSON logging across all agents. Write `README.md` with architecture diagram and quick-start instructions. Add five pre-defined subject persona fixtures exercising each use case deterministically.

**Goal:** Production-grade security hygiene. Clear onboarding for anyone cloning the repository.

---

### Phase 9 — ADK Server-Side + Mortgage Platform (stretch)

Wire up ADK's `AgentExecutor` server-side handler on the Orchestrator, replacing the plain FastAPI `POST /tasks/send` stub. ADK manages: task receipt and validation, immediate `task_id` acknowledgement, task lifecycle state machine visible to the caller, SSE streaming to external consumers, and task cancellation. The internal pipeline is unchanged — ADK wraps only the server-side protocol layer.

Build the `mortgage-platform` service — a minimal ADK agent (~60 lines) at `:9000`. On startup it discovers VerifyIQ's Agent Card, confirms the `verify_subject` skill, dispatches an A2A task with a sample mortgage subject, connects to the SSE stream, and logs progress events and the final `VerificationDecision` to stdout. Added to `docker-compose.yml` with `depends_on: orchestrator`; started explicitly (`docker compose up mortgage-platform`) so it does not interrupt the default UC-1–4 workflow.

**Tests:** All UC-5 ADK server-side tests from Section 10: `task_id` returned immediately; `GET /tasks/{task_id}` returns `working` while pipeline runs; SSE delivers progress to Mortgage Platform; final result is `completed` with `VerificationDecision`; `correlation_id` propagates into all internal `agent_tasks` rows; re-sending the task produces a new independent pipeline run.

**Goal:** VerifyIQ is a true A2A peer. External teams can call it without knowledge of its internal pipeline. ADK is used for its correct purpose: managing the server-side protocol for callers you don't control.

---

## 15. Open Questions & Future Considerations

### Deferred Decisions

**A2A push notifications (webhooks).** Current design uses client-initiated SSE. A2A supports agent-initiated push to a client-supplied webhook. The LangGraph Employment Agent is the natural first candidate.

**Registry health heartbeat.** Agents currently deregister on graceful shutdown only. A periodic `PUT /agents/{url_hash}/health` heartbeat with TTL-based `unhealthy` marking would handle ungraceful shutdowns.

**Multi-subject batch requests.** One subject per request currently. Batch mode would stress-test fan-out and Registry resolution at higher concurrency.

**Circuit breaker.** If an agent fails repeatedly across requests, the Orchestrator currently retries each time. A circuit breaker (trip after N consecutive failures, half-open after cooldown) would skip known-bad agents without even attempting. The Registry `health` field already provides a manual version — automating it is a natural extension.

**Chaos / resilience testing.** Randomly failing or slowing one agent during a test run and verifying Orchestrator behavior (timeouts, retries, partial results) is high-value but requires a test harness for container-level fault injection. Phase 9 stretch goal alongside ADK server-side work.

### Known Simplifications vs. Production

- SSNs are simulated random strings — no real PII
- Credit and employment data is LLM-generated fiction
- Consent simulated by a checkbox
- No rate limiting on agent endpoints
- No auth between agents until Phase 8
- Registry unauthenticated until Phase 8
- SQLite — no concurrent write protection

---

## 16. Why A2A — Not Hardwired Workflows, MCP Tools, LangGraph; and ADK's Correct Role

This section is the complete architectural rationale for every technology choice at the inter-agent boundary. It covers three alternatives that were ruled out (hardwired workflows, MCP tools, LangGraph) and explains exactly where ADK fits — not as an alternative to reject, but as the right tool for the specific server-side problem it solves.

### The Organizing Question

Every technology choice at the inter-agent level follows a single question:

> **Does this cross an organizational or deployment boundary?**

If yes → A2A. If no, ask: does it need stateful branching logic? If yes → LangGraph internally. If no → plain Python functions.

This rule is applied consistently throughout VerifyIQ and generalizes to any multi-agent system design.

---

### A2A: For Organizational Boundaries

A2A is designed for the case where agents are **owned and operated independently** — different teams, different repositories, different deployment pipelines. The key properties that make A2A the right choice at this boundary:

- Each service runs in its own process and container, deployable independently
- Communication is over HTTP — any language, any framework can implement it
- The Agent Card is a formal contract: the Orchestrator learns what a service can do without reading its code
- Agent Cards are served at a standardized well-known URL — any compliant agent is discoverable
- The task protocol (submit → poll → collect) handles network failures, timeouts, and retries explicitly

In VerifyIQ, the four data agents simulate real bureau organizations. In a production deployment, Equifax's team would operate their agent independently. The Orchestrator team has no access to Equifax's codebase. A2A is the only workable inter-agent protocol at this boundary.

**VerifyIQ is designed to accept new agents from other teams in future.** Any team that builds a compliant A2A agent with a relevant skill (`fraud_score`, `income_verification`, `sanctions_check`) and registers it with the Registry becomes immediately available to the Orchestrator — zero Orchestrator code changes required.

---

### Why Not a Hardwired Workflow?

The simplest alternative is a hardwired workflow — direct `asyncio.gather` calls to known agent functions, or a LangGraph graph that imports all agent logic as nodes. This works and is faster to build when one team owns everything.

The problem is what it costs at the organizational boundary. A hardwired Orchestrator must:

- **Know the implementation** of every agent — either by importing its Python module or by having its URL and schema hardcoded in the Orchestrator's source
- **Redeploy together** with any agent that changes — if Equifax changes its response schema, the Orchestrator code must be updated and redeployed in the same release
- **Be rewritten** to add or replace an agent — adding a fraud detection agent means editing the Orchestrator's fan-out logic, not just registering a new service

In a domain where each "agent" represents a separate organization (Equifax, The Work Number, Experian International), a hardwired workflow is not an architectural option — it would require merging all organizations' code into one deployable. A2A exists precisely to replace this coupling with a protocol contract.

Within VerifyIQ, the Orchestrator does execute a fixed DAG — the order of operations is deterministic per use case type. But the *which agent executes each step* is resolved at runtime via the Registry. The workflow structure is hardwired; the agent identities are not.

---

### Why Not MCP Tools Here?

MCP tools are designed for a different scope: **one agent calling capabilities that live in the same process or on the same team's infrastructure**. An MCP tool is a Python function with a typed interface — fast, synchronous, no network overhead, no service boundary.

If the Equifax credit check were an MCP tool:

```python
# MCP tool approach — Equifax logic runs inside the Orchestrator's process
@tool
def get_credit_report(subject_id: str) -> dict:
    # Equifax's proprietary logic runs HERE, in the Orchestrator's container
    ...
```

This would require Equifax's logic to be imported into and deployed with the Orchestrator. The teams cannot work independently. The Orchestrator cannot be updated without redeploying Equifax's logic and vice versa. There is no organizational boundary.

MCP tools are correctly used inside individual agents for their internal operations — e.g. a tool that looks up a cached result, formats a prompt, or validates a schema. In VerifyIQ these are implemented as plain Python functions (same concept, without the MCP wire protocol) precisely because they are internal to each agent's team.

---

### Why Not LangGraph at the Orchestration Level?

LangGraph is a stateful graph execution framework — ideal when an agent needs to maintain conversational context across turns, branch conditionally based on intermediate results, or loop until a condition is met. These are exactly the characteristics of the Employment Agent's internal logic (check current employer → branch → check prior employer → verify income).

But LangGraph at the orchestration level would mean:

```python
# LangGraph orchestration — all agents must be Python, same process, same codebase
graph = StateGraph(VerificationState)
graph.add_node("equifax", run_equifax_logic)    # Equifax's code in your graph
graph.add_node("employment", run_employment_logic)
graph.add_node("synthesis", run_synthesis_logic)
```

All agents must be Python. All agents must be imported into the same codebase. Independent deployment is impossible — the entire graph redeploys as a unit. The Equifax team and the Employment team cannot ship independently.

LangGraph is correctly used **within** the Employment Agent because all the nodes (`lookup_current_employer`, `verify_income`, etc.) are owned by the same team, live in the same container, and are implementation details hidden behind the A2A task protocol. The Orchestrator never knows LangGraph is involved.

---

### ADK — Server-Side Role

ADK is Google's Python framework for building A2A-compliant agents. It provides Agent Card generation, an A2A client for dispatching tasks, a task state machine, and an `AgentExecutor` abstraction for handling inbound tasks.

For VerifyIQ the question is not whether to use ADK, but **where** it earns its cost. The answer is the server side — specifically the Orchestrator's `POST /tasks/send` endpoint when it receives tasks from external orchestrators it does not control (UC-5: Mortgage Platform).

Here is what ADK provides compared to plain `httpx` for each capability:

| ADK capability | Outbound (Orchestrator → data agents) | Inbound (external → Orchestrator) |
|---|---|---|
| A2A client (task dispatch) | `TaskDispatcher` with `httpx` — ~30 lines, already specced | Not applicable |
| Agent Card serving | Static JSON served by FastAPI route — trivial | Same |
| Task lifecycle state machine | `TaskManager` in SQLite — already specced | **ADK handles this for the caller** |
| SSE streaming to caller | `SSEStreamer` pushing to CLI — already specced | **ADK manages backpressure, reconnects to external caller** |
| Task cancellation from caller | Not needed for internal agents | **ADK handles inbound cancellation signals** |

The outbound column is already fully specced — plain code does the job well and teaches more. The inbound column is where ADK saves non-trivial implementation: managing task state visible to a caller you don't control, streaming SSE to an external consumer with proper reconnect handling, and processing task cancellation requests from the caller are all protocol-level concerns that ADK abstracts correctly.

Without ADK on the inbound path, the team would need to hand-roll the full A2A server protocol — including edge cases like: what happens if the Mortgage Platform disconnects mid-stream and reconnects? How is task cancellation signalled and propagated? These are the problems ADK was built to solve. The outbound dispatch is simple enough to write directly; the inbound server protocol is not.

**Where ADK appears in the codebase:**
- `agents/orchestrator/main.py` — ADK's `AgentExecutor` wraps the `POST /tasks/send` handler
- `agents/mortgage-platform/main.py` — A second ADK agent acting as the external caller (Phase 9)
- All four data agents — plain FastAPI; they are simple enough that ADK adds no value on their server side

---

### The Orchestrator Does Not Multi-Turn

One final clarification: the Orchestrator does not maintain conversational state or reason dynamically about which agents to call. It executes a fixed DAG per use case type. There is no LLM in the Orchestrator itself.

This is the correct design for a deterministic pipeline. If the workflow required dynamic reasoning — "based on the credit report, decide whether to also call a bankruptcy detail agent" — then an LLM-driven Orchestrator with MCP tools or A2A agents would be appropriate. For VerifyIQ's fixed use cases, a deterministic DAG is simpler, more testable, and easier to debug.

---

### Summary Table

| Dimension | A2A (inter-agent) | Hardwired workflow | MCP Tools | LangGraph |
|---|---|---|---|---|
| **Scope** | Across org/deployment boundaries | Same codebase, any boundary | Within one agent, same team | Within one agent, stateful branching |
| **Lives in** | Separate process + container | Same process or tightly coupled | Same process | Same process |
| **Called via** | HTTP over network | Direct function call / import | Local function call | Graph node execution |
| **Team ownership** | Each agent independently owned | One team owns everything | Same team owns all tools | Same team owns all nodes |
| **Deploy independently** | ✅ Yes | ❌ No | ❌ No | ❌ No |
| **Language agnostic** | ✅ Any language | ❌ Must share language | ❌ Must match agent language | ❌ Python only |
| **Where in VerifyIQ** | Orchestrator → all agents | ❌ Not used | Inside each agent (plain Python) | Inside Employment Agent |
| **Best for** | Fixed pipelines across org boundaries | Single-team owned automation | Internal agent operations | Internal branching state machines |

---

*Document version: 1.7 | Last updated: 2026-04-14 | Status: Ready for implementation*
