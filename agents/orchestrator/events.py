"""SSE event type constants and payload builders for the verification pipeline."""

from typing import Any, Literal

# All 9 event types emitted during a verification pipeline
EVENT_TYPE_AGENTS_RESOLVED: Literal["agents_resolved"] = "agents_resolved"
EVENT_TYPE_AGENT_STARTED: Literal["agent_started"] = "agent_started"
EVENT_TYPE_AGENT_COMPLETED: Literal["agent_completed"] = "agent_completed"
EVENT_TYPE_AGENT_FAILED: Literal["agent_failed"] = "agent_failed"
EVENT_TYPE_AGENT_SKIPPED: Literal["agent_skipped"] = "agent_skipped"
EVENT_TYPE_SYNTHESIS_STARTED: Literal["synthesis_started"] = "synthesis_started"
EVENT_TYPE_SYNTHESIS_COMPLETED: Literal["synthesis_completed"] = "synthesis_completed"
EVENT_TYPE_COMPLETED: Literal["completed"] = "completed"
EVENT_TYPE_FAILED: Literal["failed"] = "failed"


def build_agents_resolved_payload(agents: list[str]) -> dict[str, Any]:
    """Build payload for agents_resolved event."""
    return {"agents": agents}


def build_agent_started_payload(agent: str, skill: str) -> dict[str, Any]:
    """Build payload for agent_started event."""
    return {"agent": agent, "skill": skill}


def build_agent_completed_payload(agent: str, status: str) -> dict[str, Any]:
    """Build payload for agent_completed event."""
    return {"agent": agent, "status": status}


def build_agent_failed_payload(agent: str, status: str, error: str) -> dict[str, Any]:
    """Build payload for agent_failed event."""
    return {"agent": agent, "status": status, "error": error}


def build_agent_skipped_payload(agent: str, reason: str) -> dict[str, Any]:
    """Build payload for agent_skipped event."""
    return {"agent": agent, "reason": reason}


def build_synthesis_started_payload(agent: str) -> dict[str, Any]:
    """Build payload for synthesis_started event."""
    return {"agent": agent}


def build_synthesis_completed_payload(decision: str, confidence: str) -> dict[str, Any]:
    """Build payload for synthesis_completed event."""
    return {"decision": decision, "confidence": confidence}


def build_completed_payload(task_id: str, decision: str) -> dict[str, Any]:
    """Build payload for completed event."""
    return {"task_id": task_id, "decision": decision}


def build_failed_payload(task_id: str, error: str) -> dict[str, Any]:
    """Build payload for failed event."""
    return {"task_id": task_id, "error": error}
