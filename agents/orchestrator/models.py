# agents/orchestrator/models.py
from typing import Literal, Optional

from pydantic import BaseModel


class VerificationRequest(BaseModel):
    """Input to POST /verify. Drives which agents are invoked and how synthesis weighs them."""

    subject_name: str
    subject_id: str  # Simulated ID — no real SSNs
    use_case: Literal["mortgage", "rental", "auto", "hire"]
    has_foreign_addr: bool  # True → Intl Agent is invoked
    consent: bool


class VerifyResponse(BaseModel):
    """Returned immediately from POST /verify. Client uses task_id to poll status."""

    task_id: str
    correlation_id: str
    stream_url: str  # e.g. "/verify/{task_id}/stream" — active in Phase 6


class TaskStatusResponse(BaseModel):
    """Response from GET /verify/{task_id}."""

    task_id: str
    correlation_id: str
    status: Literal["submitted", "working", "completed", "failed"]
    decision: Optional[str] = None  # approve / review / decline, populated on completion
    created_at: str
    completed_at: Optional[str] = None
