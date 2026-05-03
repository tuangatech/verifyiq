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
    """Trigger a verification workflow against VerifyIQ via A2A protocol.

    1. Discover the Orchestrator's agent card
    2. Submit an A2A task with skill=verify_subject
    3. Poll until terminal status
    4. Return the final result
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

    # 3. Submit and wait
    log.info("submitting_verification", task_id=task["task_id"], subject=body.subject_name)
    result = await client.send_and_wait(task, poll_interval=2.0, timeout=90.0)

    log.info("verification_result", task_id=task["task_id"], status=result.get("status"))

    return result
