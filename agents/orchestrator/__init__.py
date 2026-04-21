# agents/orchestrator/__init__.py
import sys
sys.path.insert(0, "/app")

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

import structlog

from agents.shared.a2a_types import A2ATask, AgentOutcome
from agents.shared.registry_client import register_with_registry, deregister_from_registry
from .db import TaskManager
from .dispatcher import TaskDispatcher
from .models import VerificationRequest, VerifyResponse, TaskStatusResponse
from .resolver import AgentResolver, NoCandidateAgentError
from .workflow import get_agent_plan

logger = structlog.get_logger()

agent_card_path = Path(__file__).parent / "agent_card.json"
with open(agent_card_path) as f:
    AGENT_CARD = json.load(f)

_url_hash: str | None = None
resolver = AgentResolver()
task_manager = TaskManager()
dispatcher = TaskDispatcher()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Init DB, register with Registry on startup; deregister on graceful shutdown."""
    global _url_hash
    task_manager.init_db()
    _url_hash = await register_with_registry(
        str(Path(__file__).parent / "agent_card.json")
    )
    yield
    await deregister_from_registry(_url_hash)


app = FastAPI(lifespan=lifespan)


@app.get("/.well-known/agent.json")
def agent_card():
    """Serve the Agent Card for A2A discovery."""
    return AGENT_CARD


@app.get("/health")
def health():
    """Liveness check."""
    return {"status": "healthy", "agent": "orchestrator", "port": 8000}


@app.post("/verify")
async def verify(body: VerificationRequest) -> VerifyResponse:
    """Accept a verification request, persist it, and kick off the pipeline in the background."""
    task_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    task_manager.create_verification_request(task_id, correlation_id, body)
    # Fire and forget — caller polls GET /verify/{task_id} for status
    asyncio.create_task(run_verification(task_id, correlation_id, body))
    return VerifyResponse(
        task_id=task_id,
        correlation_id=correlation_id,
        stream_url=f"/verify/{task_id}/stream",
    )


@app.get("/verify/{task_id}")
def get_verify_status(task_id: str) -> TaskStatusResponse:
    """Poll for the current status of a verification request."""
    result = task_manager.get_verification_request(task_id)
    if result is None:
        raise HTTPException(404, "Task not found")
    return TaskStatusResponse(**result)


@app.get("/verify/{task_id}/tasks")
def get_agent_tasks(task_id: str) -> list[dict]:
    """Debug endpoint: return all agent_tasks rows for a given verification request."""
    return task_manager.get_agent_tasks(task_id)


@app.get("/agents")
async def list_agents():
    """Proxy the Registry's full agent list — used by the CLI agent table."""
    return await resolver.list_all()


@app.get("/resolve/{skill}")
async def resolve_skill(skill: str):
    """Debug endpoint: resolve a skill to an agent URL; return error JSON on miss."""
    try:
        url = await resolver.find(skill)
        return {"skill": skill, "url": url}
    except NoCandidateAgentError as e:
        return {"skill": skill, "error": str(e)}


async def run_verification(
    task_id: str, correlation_id: str, body: VerificationRequest
) -> None:
    """Full orchestration pipeline: fan-out → collect → chain to synthesis."""
    log = logger.bind(task_id=task_id, correlation_id=correlation_id)

    try:
        task_manager.update_request_status(task_id, "working")

        # 1. Determine which agents to invoke
        plan = get_agent_plan(body.use_case, body.has_foreign_addr)
        log.info("agent_plan_resolved", parallel=[n for _, n, _ in plan["parallel"]])

        # 2. Resolve all parallel agent URLs from Registry
        skills_to_resolve = [skill for skill, _, _ in plan["parallel"]]
        skill_urls = await resolver.find_all(skills_to_resolve)

        # 3. Build and dispatch all parallel tasks concurrently
        async def dispatch_one(
            skill: str, agent_name: str, required: bool
        ) -> AgentOutcome:
            """Dispatch a single agent task and record it in the DB."""
            agent_url = skill_urls[skill]
            agent_task_id = str(uuid.uuid4())
            a2a_task = A2ATask(
                task_id=agent_task_id,
                correlation_id=correlation_id,
                skill=skill,
                input=body.model_dump(),
                attempt=1,
            )
            started_at = datetime.now(timezone.utc).isoformat()
            task_manager.create_agent_task(
                agent_task_id, task_id, correlation_id,
                agent_name, skill, body.model_dump(), 1, started_at,
            )

            outcome = await dispatcher.dispatch(agent_url, a2a_task, agent_name)

            ended_at = datetime.now(timezone.utc).isoformat()
            error_dict = outcome.error.model_dump() if outcome.error else None
            task_manager.complete_agent_task(
                agent_task_id, outcome.status,
                outcome.artifact, error_dict, ended_at,
            )
            return outcome

        # 4. Fan-out: asyncio.gather across all parallel agents
        parallel_outcomes = await asyncio.gather(
            *[dispatch_one(skill, name, req) for skill, name, req in plan["parallel"]]
        )
        outcomes: list[AgentOutcome] = list(parallel_outcomes)

        # 5. Add skipped agent outcomes for agents not in the parallel plan
        all_possible = {
            "credit_score": "equifax",
            "employment_status": "employment",
            "international_credit_score": "intl",
        }
        invoked_skills = {skill for skill, _, _ in plan["parallel"]}
        for skill, agent_name in all_possible.items():
            if skill not in invoked_skills:
                skipped_task_id = str(uuid.uuid4())
                now_ts = datetime.now(timezone.utc).isoformat()
                task_manager.create_agent_task(
                    skipped_task_id, task_id, correlation_id,
                    agent_name, skill, {}, 0, now_ts,
                )
                task_manager.complete_agent_task(
                    skipped_task_id, "skipped", None, None, now_ts,
                )
                outcomes.append(AgentOutcome(
                    agent_name=agent_name,
                    skill=skill,
                    status="skipped",
                    artifact=None,
                    error=None,
                ))

        log.info(
            "parallel_phase_complete",
            outcomes={o.agent_name: o.status for o in outcomes},
        )

        # 6. Sequential chain: dispatch to Risk Synthesis
        synthesis_skill, synthesis_name, _ = plan["sequential"][0]
        synthesis_url = await resolver.find(synthesis_skill)

        synthesis_input = {
            "outcomes": [o.model_dump() for o in outcomes],
            "use_case": body.use_case,
        }
        synthesis_task_id = str(uuid.uuid4())
        synthesis_a2a_task = A2ATask(
            task_id=synthesis_task_id,
            correlation_id=correlation_id,
            skill=synthesis_skill,
            input=synthesis_input,
            attempt=1,
        )
        started_at = datetime.now(timezone.utc).isoformat()
        task_manager.create_agent_task(
            synthesis_task_id, task_id, correlation_id,
            synthesis_name, synthesis_skill, synthesis_input, 1, started_at,
        )

        synthesis_outcome = await dispatcher.dispatch(
            synthesis_url, synthesis_a2a_task, synthesis_name
        )

        ended_at = datetime.now(timezone.utc).isoformat()
        error_dict = synthesis_outcome.error.model_dump() if synthesis_outcome.error else None
        task_manager.complete_agent_task(
            synthesis_task_id, synthesis_outcome.status,
            synthesis_outcome.artifact, error_dict, ended_at,
        )

        # 7. Determine final status and decision
        if synthesis_outcome.status == "completed" and synthesis_outcome.artifact:
            decision = synthesis_outcome.artifact.get("decision")
            task_manager.update_request_status(task_id, "completed", decision=decision)
            log.info("pipeline_completed", decision=decision)
        else:
            task_manager.update_request_status(task_id, "failed")
            log.warning("pipeline_failed", synthesis_status=synthesis_outcome.status)

    except Exception as exc:
        log.error("pipeline_exception", error=str(exc))
        task_manager.update_request_status(task_id, "failed")
