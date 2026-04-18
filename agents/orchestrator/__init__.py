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

from agents.shared.a2a_types import A2ATask
from agents.shared.registry_client import register_with_registry, deregister_from_registry
from .db import TaskManager
from .dispatcher import TaskDispatcher
from .models import VerificationRequest, VerifyResponse, TaskStatusResponse
from .resolver import AgentResolver, NoCandidateAgentError

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
    """Background pipeline: resolve Equifax agent, dispatch one task, record outcome.

    Phase 3 scope: single Equifax round-trip only. Full fan-out is Phase 5.
    """
    try:
        task_manager.update_request_status(task_id, "working")

        # Resolve Equifax agent URL from Registry
        equifax_url = await resolver.find("credit_score")

        # Build the A2A task
        agent_task_id = str(uuid.uuid4())
        a2a_task = A2ATask(
            task_id=agent_task_id,
            correlation_id=correlation_id,
            skill="credit_score",
            input=body.model_dump(),
            attempt=1,
        )

        # Record the task row before dispatch so it exists even if the agent never responds
        started_at = datetime.now(timezone.utc).isoformat()
        task_manager.create_agent_task(
            agent_task_id, task_id, correlation_id,
            "equifax", "credit_score", body.model_dump(), 1, started_at,
        )

        # Dispatch to the Equifax agent
        outcome = await dispatcher.dispatch(equifax_url, a2a_task, "equifax")

        # Record the result
        ended_at = datetime.now(timezone.utc).isoformat()
        error_dict = outcome.error.model_dump() if outcome.error else None
        task_manager.complete_agent_task(
            agent_task_id, outcome.status,
            outcome.artifact, error_dict, ended_at,
        )

        task_manager.update_request_status(task_id, "completed")

    except Exception:
        # Any unhandled error → mark the request as failed
        task_manager.update_request_status(task_id, "failed")
