# agents/employment/main.py
import sys
sys.path.insert(0, "/app")

import json
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager

import structlog

from fastapi import Depends, FastAPI, HTTPException

from agents.shared.a2a_types import A2ATask, A2ATaskResult, AgentError
from agents.shared.auth import require_auth
from agents.shared.logging import configure_logging
from agents.shared.registry_client import register_with_registry, deregister_from_registry

configure_logging("employment")
logger = structlog.get_logger()
from agents.shared.schemas import EmploymentArtifact
from agents.employment.graph import build_employment_graph

agent_card_path = Path(__file__).parent / "agent_card.json"
with open(agent_card_path) as f:
    AGENT_CARD = json.load(f)

_url_hash: str | None = None
task_store: dict[str, dict] = {}
employment_graph = build_employment_graph()


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
    return {"status": "healthy", "agent": "employment", "port": 8002}


@app.post("/tasks/send")
async def tasks_send(task: A2ATask, _token: str = Depends(require_auth)) -> A2ATaskResult:
    """Accept an A2A task, run the LangGraph employment verification pipeline, validate, and return.

    Error paths:
    - Graph execution exception → GRAPH_EXECUTION_FAILED
    - Node-level error in state → LLM_VALIDATION_FAILED
    - Pydantic validation of final artifact → LLM_VALIDATION_FAILED
    """
    log = logger.bind(task_id=task.task_id, correlation_id=task.correlation_id, skill=task.skill)
    log.info("task_received")
    started_at = datetime.now(timezone.utc).isoformat()

    # Build initial state from task.input
    initial_state: dict = {
        "subject_id": task.input.get("subject_id", "unknown"),
        "subject_name": task.input.get("subject_name", "Unknown Subject"),
        "use_case": task.input.get("use_case", "mortgage"),
        "today": datetime.now(timezone.utc).date().isoformat(),
    }

    try:
        # Run the graph
        terminal_state = await employment_graph.ainvoke(initial_state)

        # Check for node-level errors that triggered a terminal path
        if "error" in terminal_state:
            ended_at = datetime.now(timezone.utc).isoformat()
            result = A2ATaskResult(
                task_id=task.task_id,
                correlation_id=task.correlation_id,
                status="failed",
                artifact=None,
                error=AgentError(
                    code="LLM_VALIDATION_FAILED",
                    message=terminal_state["error"],
                    retryable=False,
                ),
                started_at=started_at,
                ended_at=ended_at,
            )
            task_store[task.task_id] = result.model_dump()
            return result

        # Build artifact from terminal state
        try:
            artifact_data = terminal_state.get("artifact", {})
            artifact = EmploymentArtifact(**artifact_data)
        except Exception as e:
            ended_at = datetime.now(timezone.utc).isoformat()
            result = A2ATaskResult(
                task_id=task.task_id,
                correlation_id=task.correlation_id,
                status="failed",
                artifact=None,
                error=AgentError(
                    code="LLM_VALIDATION_FAILED",
                    message=f"Artifact validation failed: {e}",
                    retryable=False,
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

    except Exception as exc:
        # Graph execution exception (not caught inside nodes)
        ended_at = datetime.now(timezone.utc).isoformat()
        result = A2ATaskResult(
            task_id=task.task_id,
            correlation_id=task.correlation_id,
            status="failed",
            artifact=None,
            error=AgentError(
                code="GRAPH_EXECUTION_FAILED",
                message=str(exc),
                retryable=False,
            ),
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
