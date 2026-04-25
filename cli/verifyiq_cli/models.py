# cli/verifyiq_cli/models.py
"""Lightweight response models for CLI display. No dependency on agents/shared/."""

from typing import Optional

from pydantic import BaseModel


class VerifyResponse(BaseModel):
    """Returned from POST /verify."""

    task_id: str
    correlation_id: str
    stream_url: str


class TaskStatus(BaseModel):
    """Returned from GET /verify/{task_id}."""

    task_id: str
    correlation_id: str
    status: str
    decision: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


class HistoryItem(BaseModel):
    """One row from GET /verify/history."""

    task_id: str
    correlation_id: str
    subject_name: str
    use_case: str
    status: str
    decision: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


class AgentInfo(BaseModel):
    """One entry from GET /agents."""

    name: str
    url: str
    skills: list[str] | str
    health: str = "unknown"
    avg_latency_ms: Optional[float] = None
    last_seen: Optional[str] = None
