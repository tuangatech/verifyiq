# I Built a Multi-Agent System Where AI Agents Talk to Each Other Like Microservices

**What I learned building a credit verification platform with 6 independent AI agents, LangGraph, and a protocol called A2A**

---

I wanted to understand how AI agents can work together across team and deployment boundaries — not as functions in the same codebase, but as truly independent services that discover each other, send tasks, handle failures, and produce a combined result.

So I built VerifyIQ: a simulated credit and employment verification platform where a loan officer submits a person's info, and multiple AI agents — each running in its own Docker container — collaborate to produce a risk decision. Think of it as microservices, but the services are LLM-powered agents that talk to each other through a standardized protocol.

Here's what the architecture looks like:

```
CLI (terminal)  -->  Orchestrator (:8000)  -->  Agent Registry (:8099)
                           |
              +------------+------------+
              |            |            |
         Equifax      Employment     Intl          -->  Risk Synthesis
         (:8001)      (:8002)       (:8003)              (:8004)
```

Six services. Four AI agents calling LLMs. One orchestrator coordinating everything. One registry so agents can find each other. All communication over HTTP using a protocol called Agent-to-Agent (A2A).

The whole thing runs with `docker compose up`.

---

## Why Not Just Chain Some LLM Calls?

The simplest version of this project is maybe 50 lines of Python: call an LLM for credit data, call another for employment, call a third to synthesize, done.

But that misses the point entirely.

In the real world, credit bureaus like Equifax, TransUnion, and Experian are separate companies. They have their own codebases, their own teams, their own deployment pipelines. You can't import Equifax's logic into your codebase. You call their API, get a response, and deal with it — including when it fails or times out.

I wanted to build something that reflects that reality. Each agent in VerifyIQ:

- Runs in its own container
- Has its own LLM prompts and validation logic
- Registers itself with a central registry on startup
- Has zero knowledge of other agents
- Can be replaced, redeployed, or updated independently

The Orchestrator doesn't even know agent URLs. It asks the Registry: "Who can do `credit_score`?" and gets an answer at runtime. If someone deploys a faster credit agent tomorrow, the Orchestrator picks it up automatically.

---

## Where Does the Data Come From? LLMs as Data Simulators

A fair question: if this is a learning project, where does the credit, employment, and international data actually come from?

There are no real Equifax, Experian, or payroll APIs here. Instead, each agent uses an LLM (via OpenRouter) to **generate realistic but fictional data**. The Equifax agent doesn't call a credit bureau — it prompts an LLM with a subject's persona and asks it to generate a plausible credit profile: a FICO score, open accounts, utilization percentage, derogatory marks. The Employment agent does the same for employer records, income, and tenure. The International agent generates foreign credit profiles complete with local scores mapped to US equivalents.

Why use an LLM instead of just hardcoding fake data? Two reasons:

1. **Variety.** Hardcoded stubs return the same data every time. LLM-generated data varies per subject, so the Risk Synthesis agent actually has something interesting to reason about. A high-credit-score subject with stable employment gets a different decision than a subject with derogatory marks and a recent job change.

2. **The real pattern is identical.** In production, each agent would call a real bureau API and parse the response. Here, the agent calls an LLM and parses the response. The surrounding code — validation, retry on bad data, error handling, structured output — is exactly the same. Swap the LLM call for a real API call and the agent's architecture doesn't change.

The important thing is what happens *around* the data: how agents validate it, how the Orchestrator handles it when it fails, how Risk Synthesis reasons over partial results. That's where the A2A patterns live, and those patterns are real regardless of where the data comes from.

---

## The A2A Protocol: How Agents Talk

All inter-agent communication uses a simple task protocol. The Orchestrator sends a task, the agent does work, and returns a result. That's it.

```python
class A2ATask(BaseModel):
    task_id: str
    correlation_id: str    # same across ALL tasks in one request
    skill: str             # what you want the agent to do
    input: dict
    timeout_ms: int = 30000
    attempt: int = 1       # 1 = first try, 2 = retry

class A2ATaskResult(BaseModel):
    task_id: str
    correlation_id: str
    status: Literal["completed", "failed", "timed_out"]
    artifact: dict | None
    error: AgentError | None
    started_at: str
    ended_at: str
```

Every agent exposes `POST /tasks/send` and `GET /tasks/{task_id}`. That's the entire contract. The Orchestrator doesn't care if the agent uses LangGraph, a simple function, or a hamster wheel internally. It sends a task, gets a result.

The `correlation_id` is critical — it's the same UUID across every task in a single verification request. When something goes wrong, one grep across all container logs reconstructs the entire execution trace:

```bash
docker compose logs | grep "corr-abc123"
```

No distributed tracing system needed. Just one ID flowing everywhere.

---

## Fan-Out: Calling Agents in Parallel

When someone requests a mortgage verification, the Orchestrator needs data from Equifax (credit), Employment (income), and International (foreign credit) agents simultaneously. Calling them one by one would mean 30+ seconds of waiting. Calling them in parallel means we wait only as long as the slowest agent.

```python
async def dispatch_one(skill, agent_name, required) -> AgentOutcome:
    agent_url = skill_urls[skill]
    a2a_task = A2ATask(
        task_id=str(uuid.uuid4()),
        correlation_id=correlation_id,
        skill=skill,
        input=body.model_dump(),
    )
    return await dispatcher.dispatch(agent_url, a2a_task, agent_name)

# Fire all three simultaneously
parallel_outcomes = await asyncio.gather(
    *[dispatch_one(skill, name, req) for skill, name, req in plan["parallel"]]
)
```

`asyncio.gather` dispatches all tasks concurrently. Results arrive as they complete. The test suite verifies that all parallel agents start within 2 seconds of each other — they're genuinely concurrent, not accidentally sequential.

---

## The Part That Took the Longest: Handling Failure

In a demo, everything works. In reality, agents fail, time out, and return garbage. The design decision I'm most proud of is: **always produce a report, even when things go wrong.**

The system tracks four distinct task statuses:

```
submitted -> working -> completed
                    \-> failed       (agent returned an error)
                    \-> timed_out    (didn't respond in 30s)
          \-> skipped               (not needed for this use case)
```

`timed_out` and `failed` are deliberately separate. A timeout means the infrastructure hiccupped — don't penalize the person being verified. A failure might mean bad data or a logic error — that's a genuine data gap.

When results are collected, the Orchestrator packages everything — successes AND failures — into an outcome bundle:

```python
[
    { "agent": "equifax",    "status": "completed", "artifact": {...} },
    { "agent": "employment", "status": "completed", "artifact": {...} },
    { "agent": "intl",       "status": "timed_out", "artifact": null,
      "error": { "code": "TIMEOUT", "message": "...", "retryable": true } }
]
```

This entire bundle goes to the Risk Synthesis agent. The synthesis LLM prompt explicitly teaches the model how to reason over partial data:

```
Outcome status interpretation:
- "completed" -- use the artifact data for your analysis
- "timed_out" -- transient infrastructure issue; reduce confidence;
                 do NOT penalise the subject
- "failed"    -- data gap; note in risk_flags; reduce confidence
- "skipped"   -- agent was not invoked; do not mention it
```

The result? When the international agent times out, the system doesn't crash or return nothing. It says: "We can't assess foreign credit risk at this time. Confidence reduced. Recommend manual review." That's exactly what a real risk analyst would say.

---

## LangGraph Inside an A2A Agent

The Employment Agent is the most interesting one architecturally. Employment verification has genuine branching logic: check if someone is currently employed, and if not, look up their prior employer. Then verify income and calculate tenure.

This is exactly what LangGraph is good at — stateful graphs with conditional edges:

```python
graph = StateGraph(EmploymentGraphState)

graph.add_node("lookup_current_employer", lookup_current_employer)
graph.add_node("lookup_prior_employer", lookup_prior_employer)
graph.add_node("verify_income", verify_income)
graph.add_node("calculate_tenure", calculate_tenure)
graph.add_node("build_artifact", build_artifact)

graph.set_entry_point("lookup_current_employer")

def route_after_employer_check(state):
    if state.get("error"):
        return "build_artifact"      # short-circuit on error
    if state.get("currently_employed"):
        return "verify_income"       # skip prior employer lookup
    return "lookup_prior_employer"   # fallback path

graph.add_conditional_edges(
    "lookup_current_employer",
    route_after_employer_check,
    {...}
)
```

But here's the key: **the Orchestrator has no idea LangGraph is involved.** It sends `POST /tasks/send` to the Employment agent and gets back an `A2ATaskResult`. Whether that agent uses LangGraph, a simple function, or three LLM calls in a row is an internal implementation detail.

This is the A2A principle in action: framework-agnostic interop. The Equifax agent is plain FastAPI. The Employment agent is LangGraph. They both look identical from outside.

---

## The Registry: Self-Discovery Without Hardcoded URLs

Every agent registers itself on startup:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: register with the Registry
    async with httpx.AsyncClient() as client:
        await client.post(f"{registry_url}/register", json={
            "name": "Equifax Credit Agent",
            "url": "http://equifax:8001",
            "skills": ["credit_score", "tradeline_summary", ...],
            ...
        })
    yield
    # Shutdown: deregister
    async with httpx.AsyncClient() as client:
        await client.delete(f"{registry_url}/agents/{url_hash}")
```

The Orchestrator resolves skills to URLs at runtime, picking the fastest healthy candidate:

```python
class AgentResolver:
    async def find(self, skill: str) -> str:
        response = await client.get(
            f"{self.registry_url}/agents", params={"skill": skill}
        )
        candidates = [a for a in response.json() if a["health"] == "healthy"]
        # Prefer agents with lower observed latency
        candidates.sort(key=lambda a: a.get("avg_latency_ms") or float("inf"))
        return candidates[0]["url"]
```

After every dispatch, the Orchestrator reports the observed latency back to the Registry. The Registry maintains an exponential moving average, so recent performance is weighted more heavily. Over time, if two agents both advertise `credit_score`, the Resolver naturally routes to the faster one.

No agent URL is ever written in the Orchestrator's code. This means:

- Deploy a new agent with a relevant skill → it's immediately available
- Replace an agent → the Orchestrator picks it up on the next request
- A faster agent appears → it gradually wins more traffic as its latency average drops
- An agent goes down → it deregisters on graceful shutdown; the Orchestrator doesn't try to call it

---

## SSE Streaming: Because Nobody Wants to Stare at a Spinner for 30 Seconds

The pipeline takes 10-30 seconds. Without live progress, you'd submit a request and just... wait. Instead, the Orchestrator streams Server-Sent Events at every milestone:

```
agents_resolved     → "Found 3 agents for this request"
agent_started       → "Dispatching to Equifax..."
agent_completed     → "Credit report received"
agent_completed     → "Employment verified"
agent_failed        → "International agent timed out"
synthesis_started   → "Running risk synthesis..."
synthesis_completed → "Decision: approve (confidence: high)"
completed           → Full report available
```

The CLI renders these as a live timeline in the terminal using Rich.

### Late connectors and replay

What if a client opens the stream after the pipeline is already half-done? They'd miss earlier events.

The solution: every SSE event is persisted to SQLite as it's emitted. When a client connects — whether midway or after completion — the streamer first replays all past events from the database in order, then switches to the live in-memory queue for new events. A client that connects after the pipeline finishes gets a pure database replay. No events are ever lost, regardless of when you tune in.

Live streaming is single-consumer — the in-memory queue is consumed destructively, so only one client can follow a running pipeline in real time. Supporting multiple live consumers would require a pub/sub pattern (one queue per subscriber) instead of one shared queue. For this project, that's fine: the CLI is the only consumer. Completed requests can be replayed by any number of clients since replay reads from SQLite, not the queue.

SSE is a standard HTTP protocol — any web frontend could consume it via the browser's `EventSource` API, or an external orchestrator could parse the events as structured JSON. The Orchestrator doesn't care who's listening.

---

## Structured Output: Making LLMs Behave

Every data agent follows the same pattern for LLM calls:

1. Build a system prompt with persona and output schema
2. Build a user prompt with the subject's details
3. Call OpenRouter with `response_format: { type: "json_object" }`
4. Validate the response against a Pydantic model
5. If validation fails, retry once with the error message appended to the prompt
6. If it fails again, return a structured error

The retry-with-correction step is surprisingly effective:

```python
async def retry_with_correction(raw_json, error_msg, seed):
    """Re-prompt the LLM with the validation error so it can self-correct."""
    correction_prompt = (
        f"Your previous output failed validation:\n{error_msg}\n\n"
        f"Fix the JSON and return ONLY valid JSON matching the schema."
    )
    # Call LLM again with the error context
    ...
```

About 90% of validation failures are trivially fixable — a missing field, a score out of range, a date in the wrong format. The LLM reads the Pydantic error and corrects it on the second try.

Pydantic schemas enforce the data contracts:

```python
class EquifaxArtifact(BaseModel):
    credit_score: int = Field(..., ge=300, le=850)
    credit_utilization_pct: float = Field(..., ge=0, le=100)
    derogatory_marks: int = Field(..., ge=0)
    # ...
```

If the LLM says the credit score is 950, Pydantic catches it. If it returns a string instead of an integer, Pydantic catches it. The schema is the contract, and it's enforced at every boundary.

---

## Use Case Routing: Not Every Agent Runs Every Time

Different verification types need different agents. A rental screening doesn't need international credit data. An international hire doesn't need Equifax.

```python
def get_agent_plan(use_case, has_foreign_addr):
    if use_case == "mortgage":
        parallel = [equifax, employment]
        if has_foreign_addr:
            parallel.append(intl)
        return {"parallel": parallel, "sequential": [synthesis]}

    if use_case in ("rental", "auto"):
        return {"parallel": [equifax, employment], "sequential": [synthesis]}

    if use_case == "hire":
        return {"parallel": [employment, intl], "sequential": [synthesis]}
```

Skills not in the plan are marked `skipped` — not `failed`, not just absent. This distinction matters because Risk Synthesis needs to know: "I didn't get international credit data because it wasn't requested" vs. "I didn't get it because the agent crashed."

Skipping happens at the **skill level**, not the agent level. If three different agents all advertise `credit_score`, and the use case doesn't need credit data, Synthesis sees one outcome: `credit_score: skipped`. Not three skipped agents. Synthesis reasons about data categories — "did I get credit data?" — not about which specific agents exist in the registry.

---

## Why a CLI Instead of a Web UI?

VerifyIQ uses a terminal CLI built with Typer and Rich — not a web frontend. This was a deliberate choice, not a shortcut.

The project's learning goal is backend: A2A orchestration, agent coordination, failure handling. A web UI would have added React, state management, WebSocket or SSE client code, and deployment complexity — none of which teaches you anything about multi-agent patterns. The CLI gives a full interactive experience (live streaming, rich tables, formatted reports) with zero frontend overhead.

More broadly, a CLI is the better tool when:

- **Your users are developers or technical operators.** Loan officers would need a web UI. But engineers debugging an agent pipeline, running verification scenarios, or inspecting audit trails work faster in a terminal.
- **The product is a backend service, not a consumer app.** VerifyIQ's real "product" is the Orchestrator's API. The CLI is a client that exercises that API. A web UI would be *another* client of the same API — it doesn't replace the CLI, it supplements it.
- **You need scriptability.** `verifyiq run mortgage-intl --json | jq '.decision'` pipes into any Unix tool. A web UI can't do that.
- **You want fast iteration on backend changes.** No build step, no bundling, no browser refresh. Change the Orchestrator, restart it, run a CLI command, see results immediately.

That said, if this were a real product for non-technical users, a web UI would be essential. The SSE streaming layer was designed to be frontend-agnostic — the same `GET /verify/{task_id}/stream` endpoint that powers the CLI's live timeline could drive a browser-based dashboard via `EventSource` with no backend changes.

---

## Challenges I Hit (And How I Solved Them)

### The LLM output format problem

LLMs are notoriously unreliable at producing valid JSON. Even with `response_format: { type: "json_object" }`, they'll occasionally:

- Add a field that isn't in the schema
- Return a credit score of 1200 (valid JSON, invalid data)
- Wrap the JSON in markdown code fences
- Mix up date formats

The fix was defense in depth: `response_format` as a first pass, Pydantic validation as a strict second pass, and one retry with the error message for self-correction. Two chances is enough. If it fails twice, return a structured error and let Synthesis deal with the data gap.

### Testing distributed agents locally

You can't easily unit test a system where the interesting behavior is agents interacting across HTTP. My approach:

- **Mock only the LLM** (using `respx` to intercept OpenRouter calls)
- **Don't mock the Registry or A2A protocol** — test with real HTTP calls
- **Phase the tests** — test the Registry alone, then the protocol alone, then orchestration patterns, then end-to-end flows

The most critical tests are the orchestration pattern tests: does fan-out actually run concurrently? Does a timeout produce the right status? Does the correlation ID survive the full pipeline? These catch the bugs that matter.

### Choosing the right tool for each layer

With LangGraph, MCP, and plain functions all available, the temptation is to use everything everywhere. I landed on one question that cuts through it: "Does this cross an organizational or deployment boundary?"

- **A2A** between services — all inter-agent communication across process/network boundaries
- **LangGraph** inside an agent — when internal logic needs stateful branching or conditional paths (the Employment agent's "currently employed?" fork). It can also handle retries, checkpointing, and human-in-the-loop, though VerifyIQ doesn't need those yet
- **Plain functions** — the default for straightforward, linear logic. VerifyIQ's agents use plain typed Python functions for their internal tools — same pattern as MCP tools, without the wire protocol overhead
- **MCP** at the tool boundary — when you need to standardize tool access (schemas, permissions, portability) across runtimes or teams. VerifyIQ doesn't use MCP since all tools are internal to single agents, but in a production system where tools are shared across teams, MCP would replace the plain functions
Rule of thumb: start with functions; promote to LangGraph when you need stateful branching; promote to A2A when you cross service boundaries.

### Why not Google ADK?

A reasonable question: Google's Agent Development Kit (ADK) provides A2A server-side handling out of the box — task lifecycle, SSE streaming, Agent Card generation. Why not use it?

ADK is a full agent framework, not an A2A library. It wants to own your agent runtime and is deeply integrated with Google's ecosystem (Gemini, Vertex AI). VerifyIQ is deliberately model-agnostic — LLMs are swapped via env vars through OpenRouter. Importing ADK to get A2A endpoints would pull in dependencies that provide zero value here.

More importantly, the A2A server code this project needed — accepting inbound tasks, managing state visible to an external caller, streaming SSE events back — turned out to be ~80 lines of plain FastAPI. The same patterns I'd already built for the outbound side (TaskDispatcher, SSEStreamer, TaskManager) worked in reverse. There was nothing left for ADK to abstract.

ADK makes sense when you're building production agents in Google's ecosystem and don't need to understand the protocol internals. For a project whose entire point is understanding those internals, it would have been counterproductive.

### Docker networking

Services inside Docker Compose communicate by container name (`http://equifax:8001`), but the CLI runs on the host and needs `http://localhost:8000`. The solution: agents register with their Docker-internal URL (which is what other agents use), and the CLI talks only to the Orchestrator on `localhost:8000`. One gateway in, Docker handles the rest.

---

## The Stack

| Layer | Tech | Why |
|---|---|---|
| CLI | Python + Typer + Rich | Scriptable, fast iteration, no frontend build step |
| Orchestrator | FastAPI | Hub routing; inbound + outbound A2A in plain FastAPI + httpx |
| Data agents | FastAPI (Employment: LangGraph) | Simple, fast, framework-agnostic |
| LLM routing | OpenRouter | One key, multiple models, swap via env var |
| Schemas | Pydantic | Strict validation at every service boundary |
| Database | SQLite | Zero-ops; single writer per DB; swap for Postgres later |
| Deployment | Docker Compose | One command, six services |

Everything runs locally. No cloud, no Kubernetes, no infrastructure complexity.

---

## What I'd Do Differently

**Start with the protocol, not the agents.** I wish I'd spent the first day just defining the A2A types and writing protocol-level tests with hardcoded stubs. Getting the task lifecycle right (especially the `timed_out` vs `failed` distinction) was more important than any LLM prompt.

**Add circuit breakers earlier.** Right now, if an agent fails repeatedly, the Orchestrator retries every time. A circuit breaker that temporarily skips known-bad agents would prevent cascading slowdowns.

**Use structured logging from day one.** I added `structlog` later and had to retrofit `correlation_id` into existing log calls. If every log line had been JSON with `correlation_id` from the start, debugging would have been smoother throughout development.

---

## Beyond Credit Verification

VerifyIQ is a simulated platform — the patterns are not. Credit verification is just one domain where independent services need to collaborate under time pressure with partial failures. The same A2A architecture applies anywhere you'd fan out to multiple providers and synthesize results: insurance claims (medical records + police reports + repair estimates), supply chain compliance (factory audits + shipping logs + customs data), or KYC onboarding (identity verification + sanctions screening + PEP checks). The agents change, the protocol doesn't. Swap the LLM prompts and Pydantic schemas, and the orchestration layer — fan-out, timeout handling, partial-result synthesis, correlation tracking — works unchanged.

---

## Takeaways

If you're building multi-agent systems, here's what I think matters:

1. **Design for failure first.** Your happy path will work. The interesting problems are: what happens when one agent is slow? When it returns garbage? When it's down entirely? Design the outcome types and error handling before writing any LLM prompts.

2. **The organizational boundary rule is real.** If different teams will own different agents, you need a protocol (like A2A), not shared code. If one team owns everything, a simple function call is fine. Don't over-engineer boundaries that don't exist, and don't under-engineer boundaries that do.

3. **Correlation IDs are the cheapest observability you'll ever add.** One UUID flowing through everything gives you 80% of what a distributed tracing system gives you, at 1% of the setup cost.

4. **LLMs need schema enforcement at the boundary.** Don't trust LLM output. Validate with Pydantic (or equivalent). Retry once with the error. Fail gracefully on the second attempt. This pattern is boring and reliable.

5. **Partial results beat no results.** Users would rather see "3 out of 4 data sources available, confidence reduced" than a generic error screen. Design your synthesis layer to reason over incomplete data.

The full source code is on GitHub. Run `docker compose up`, install the CLI, and try `verifyiq run mortgage-intl` to see the whole thing in action.

---

*Built with Python, FastAPI, LangGraph, OpenRouter, and too much coffee on weekends.*
