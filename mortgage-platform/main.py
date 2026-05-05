# mortgage-platform/main.py
"""Mortgage Platform — external A2A caller that delegates verification to VerifyIQ."""

import json
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import structlog

import httpx

from a2a_client import A2AClient

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger()

VERIFYIQ_URL = os.environ.get("VERIFYIQ_URL", "http://orchestrator:8000")
AUTH_TOKEN = os.environ.get("VERIFYIQ_AUTH_TOKEN")

agent_card_path = Path(__file__).parent / "agent_card.json"
with open(agent_card_path) as f:
    AGENT_CARD = json.load(f)

app = FastAPI()


class TriggerRequest(BaseModel):
    """Optional body for POST /trigger — overrides the default subject."""

    subject_name: str = "David Kim"
    subject_id: str = "SIM-KR-2018"
    use_case: str = "mortgage"
    has_foreign_addr: bool = True
    consent: bool = True


@app.get("/.well-known/agent.json")
def agent_card():
    """Serve the Mortgage Platform's Agent Card."""
    return AGENT_CARD


@app.get("/health")
def health():
    return {"status": "healthy", "service": "mortgage-platform", "port": 9000}


@app.post("/trigger")
async def trigger(body: Optional[TriggerRequest] = None):
    """Submit a verification task to VerifyIQ. Returns task_id for polling.

    1. Discover the Orchestrator's agent card
    2. Submit an A2A task with skill=verify_subject
    3. Return task_id + submitted status immediately
    """
    body = body or TriggerRequest()
    client = A2AClient(VERIFYIQ_URL, AUTH_TOKEN)
    log = logger.bind(service="mortgage-platform")

    # 1. Discover
    try:
        card = await client.discover()
    except Exception as exc:
        raise HTTPException(502, f"Cannot reach VerifyIQ Orchestrator: {exc}")

    skills = [s["id"] for s in card.get("skills", [])]
    if "verify_subject" not in skills:
        raise HTTPException(502, "Orchestrator does not advertise verify_subject skill")

    log.info("orchestrator_discovered", skills=skills)

    # 2. Build A2A task
    task = {
        "task_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
        "skill": "verify_subject",
        "input": body.model_dump(),
        "timeout_ms": 60000,
        "attempt": 1,
    }

    # 3. Submit (non-blocking)
    log.info("submitting_verification", task_id=task["task_id"], subject=body.subject_name)
    ack = await client.send_task(task)

    log.info("task_accepted", task_id=task["task_id"], status=ack.get("status"))

    return {"task_id": task["task_id"], "status": ack.get("status", "submitted")}


@app.get("/trigger/{task_id}")
async def poll_trigger(task_id: str):
    """Poll the status of a previously submitted verification task."""
    client = A2AClient(VERIFYIQ_URL, AUTH_TOKEN)

    try:
        result = await client.get_task(task_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(404, "Task not found")
        raise HTTPException(502, f"Upstream error: {exc.response.status_code}")
    except Exception as exc:
        raise HTTPException(502, f"Cannot reach VerifyIQ Orchestrator: {exc}")

    return result
