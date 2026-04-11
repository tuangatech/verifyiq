"""
A2A protocol types used by agents and the Orchestrator for inter-agent communication.
These flow across service boundaries — exact field names and types must be stable.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class AgentError(BaseModel):
    """Structured error on failed or timed-out tasks."""

    code: str  # e.g. "LLM_VALIDATION_FAILED", "TIMEOUT", "UPSTREAM_ERROR"
    message: str
    retryable: bool  # True for 5xx and timeout; False for schema/logic errors


class A2ATask(BaseModel):
    """Sent by the Orchestrator to a remote agent via POST /tasks/send."""

    task_id: str
    correlation_id: str  # same value for all tasks in one verification request
    skill: str
    input: dict[str, Any]
    timeout_ms: int = 30000
    attempt: int = 1


class A2ATaskResult(BaseModel):
    """Returned by a remote agent."""

    task_id: str
    correlation_id: str  # must echo back unchanged from the task
    status: Literal["completed", "failed", "timed_out"]
    artifact: Optional[dict[str, Any]] = None
    error: Optional[AgentError] = None
    started_at: str  # ISO datetime str
    ended_at: str  # ISO datetime str


class AgentOutcome(BaseModel):
    """What the Orchestrator packages per data agent before passing to Risk Synthesis."""

    agent_name: str
    skill: str
    status: Literal["completed", "failed", "timed_out", "skipped"]
    artifact: Optional[dict[str, Any]] = None
    error: Optional[AgentError] = None


class RegisterRequest(BaseModel):
    """Payload sent by each agent to POST /register on the Registry."""

    name: str
    url: str
    skills: list[str]
    version: str = "1.0.0"
    auth_schemes: list[str] = Field(default_factory=list)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    agent_card_raw: dict[str, Any]


class RegisteredAgent(BaseModel):
    """Returned by GET /agents on the Registry."""

    url_hash: str
    name: str
    url: str
    skills: list[str]
    version: str
    health: Literal["healthy", "unhealthy", "unknown"]
    avg_latency_ms: Optional[int] = None
    registered_at: str  # ISO datetime str
    last_seen: Optional[str] = None  # ISO datetime str
