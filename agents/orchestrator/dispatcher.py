# agents/orchestrator/dispatcher.py
"""Sends outbound A2A tasks to remote agents and returns AgentOutcome objects."""

import httpx

from agents.shared.a2a_types import A2ATask, A2ATaskResult, AgentError, AgentOutcome


class TaskDispatcher:
    """Dispatches a single A2A task to a remote agent via POST /tasks/send.

    Phase 3: simple HTTP POST, no retry. Retry logic (attempt 1→2 on 5xx)
    is added in Phase 5.
    """

    def __init__(self, timeout: float = 35.0):
        """Configure the HTTP timeout (slightly above AGENT_TIMEOUT_SECONDS)."""
        self.timeout = timeout

    async def dispatch(self, agent_url: str, task: A2ATask, agent_name: str) -> AgentOutcome:
        """Send task to agent_url/tasks/send and return a structured AgentOutcome."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{agent_url}/tasks/send",
                    json=task.model_dump(),
                )
                response.raise_for_status()

            result = A2ATaskResult(**response.json())
            return AgentOutcome(
                agent_name=agent_name,
                skill=task.skill,
                status=result.status,
                artifact=result.artifact,
                error=result.error,
            )

        except httpx.HTTPStatusError as e:
            # 4xx/5xx from the agent — retryable in Phase 5
            return AgentOutcome(
                agent_name=agent_name,
                skill=task.skill,
                status="failed",
                error=AgentError(
                    code="UPSTREAM_ERROR",
                    message=str(e),
                    retryable=True,
                ),
            )

        except httpx.TimeoutException:
            return AgentOutcome(
                agent_name=agent_name,
                skill=task.skill,
                status="timed_out",
                error=AgentError(
                    code="TIMEOUT",
                    message="Agent did not respond within timeout",
                    retryable=True,
                ),
            )

        except Exception as e:
            # Unexpected error (DNS failure, connection refused, etc.)
            return AgentOutcome(
                agent_name=agent_name,
                skill=task.skill,
                status="failed",
                error=AgentError(
                    code="UPSTREAM_ERROR",
                    message=str(e),
                    retryable=False,
                ),
            )
