# agents/equifax/main.py
import sys
sys.path.insert(0, "/app")

import json
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from agents.shared.a2a_types import A2ATask, A2ATaskResult, AgentError
from agents.shared.registry_client import register_with_registry, deregister_from_registry
from agents.equifax.tools import (
    build_persona_seed,
    call_llm_credit_profile,
    validate_credit_artifact,
    retry_with_correction,
)

agent_card_path = Path(__file__).parent / "agent_card.json"
with open(agent_card_path) as f:
    AGENT_CARD = json.load(f)

_url_hash: str | None = None
task_store: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Register with the Registry on startup; deregister on graceful shutdown."""
    global _url_hash
    _url_hash = await register_with_registry()
    yield
    await deregister_from_registry(_url_hash)


app = FastAPI(lifespan=lifespan)


@app.get("/.well-known/agent.json")
def agent_card():
    """Serve the Agent Card for A2A discovery."""
    return AGENT_CARD


@app.get("/health")
def health():
    return {"status": "healthy", "agent": "equifax", "port": 8001}


@app.post("/tasks/send")
async def tasks_send(task: A2ATask) -> A2ATaskResult:
    """Accept an A2A task, call LLM to generate a credit profile, validate, and return."""
    started_at = datetime.now(timezone.utc).isoformat()
    seed = build_persona_seed(task.input)

    raw = await call_llm_credit_profile(seed)
    try:
        artifact = validate_credit_artifact(raw)
    except Exception as e:
        raw = await retry_with_correction(raw, str(e), seed)
        try:
            artifact = validate_credit_artifact(raw)
        except Exception as e2:
            ended_at = datetime.now(timezone.utc).isoformat()
            result = A2ATaskResult(
                task_id=task.task_id,
                correlation_id=task.correlation_id,
                status="failed",
                artifact=None,
                error=AgentError(
                    code="LLM_VALIDATION_FAILED", message=str(e2), retryable=False
                ),
                started_at=started_at,
                ended_at=ended_at,
            )
            task_store[task.task_id] = result.model_dump()
            return result

    ended_at = datetime.now(timezone.utc).isoformat()
    result = A2ATaskResult(
        task_id=task.task_id,
        correlation_id=task.correlation_id,
        status="completed",
        artifact=artifact.model_dump(mode="json"),
        error=None,
        started_at=started_at,
        ended_at=ended_at,
    )
    task_store[task.task_id] = result.model_dump()
    return result


@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    """Retrieve a completed task result by ID, or 404 if not found."""
    if task_id not in task_store:
        raise HTTPException(404, "Task not found")
    return task_store[task_id]
