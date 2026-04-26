# agents/orchestrator/dispatcher.py
"""Sends outbound A2A tasks to remote agents and returns AgentOutcome objects."""

import asyncio
import os

import httpx
import structlog

from agents.shared.a2a_types import A2ATask, A2ATaskResult, AgentError, AgentOutcome

logger = structlog.get_logger()


class TaskDispatcher:
    """Dispatches a single A2A task to a remote agent via POST /tasks/send.

    One retry on HTTP 5xx or connection error with 1s delay. No retry on
    4xx (bad input) or timeout (agent too slow).
    """

    def __init__(self, timeout: float = 35.0):
        """Configure the HTTP timeout (slightly above AGENT_TIMEOUT_SECONDS)."""
        self.timeout = timeout
        self._auth_token = os.environ.get("VERIFYIQ_AUTH_TOKEN")

    def _auth_headers(self) -> dict[str, str]:
        """Return Authorization header if token is configured."""
        if self._auth_token:
            return {"Authorization": f"Bearer {self._auth_token}"}
        return {}

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        """Return True for transient errors that warrant a single retry."""
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code >= 500
        if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
            return True
        return False

    async def _send(self, agent_url: str, task: A2ATask) -> httpx.Response:
        """POST the task to agent_url/tasks/send and return the response."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{agent_url}/tasks/send",
                json=task.model_dump(),
                headers=self._auth_headers(),
            )
            response.raise_for_status()
            return response

    async def dispatch(self, agent_url: str, task: A2ATask, agent_name: str) -> AgentOutcome:
        """Send task to agent_url/tasks/send with one retry on 5xx/connect error."""
        log = logger.bind(
            agent=agent_name, skill=task.skill,
            correlation_id=task.correlation_id, task_id=task.task_id,
        )

        # --- Attempt 1 ---
        try:
            response = await self._send(agent_url, task)
            result = A2ATaskResult(**response.json())
            log.info("dispatch_success", attempt=1)
            return AgentOutcome(
                agent_name=agent_name,
                skill=task.skill,
                status=result.status,
                artifact=result.artifact,
                error=result.error,
            )

        except httpx.TimeoutException:
            # Timeout — no retry; the agent is too slow
            log.warning("dispatch_timeout", attempt=1)
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

        except Exception as first_error:
            if not self._is_retryable(first_error):
                # 4xx or non-retryable error — fail immediately
                log.warning("dispatch_failed_no_retry", attempt=1, error=str(first_error))
                return AgentOutcome(
                    agent_name=agent_name,
                    skill=task.skill,
                    status="failed",
                    error=AgentError(
                        code="UPSTREAM_ERROR",
                        message=str(first_error),
                        retryable=False,
                    ),
                )

            log.warning("dispatch_retrying", attempt=1, error=str(first_error))

        # --- Attempt 2 (5xx or connection error only) ---
        await asyncio.sleep(1.0)
        retry_task = task.model_copy(update={"attempt": 2})

        try:
            response = await self._send(agent_url, retry_task)
            result = A2ATaskResult(**response.json())
            log.info("dispatch_success", attempt=2)
            return AgentOutcome(
                agent_name=agent_name,
                skill=task.skill,
                status=result.status,
                artifact=result.artifact,
                error=result.error,
            )

        except Exception as second_error:
            log.error("dispatch_failed_after_retry", attempt=2, error=str(second_error))
            return AgentOutcome(
                agent_name=agent_name,
                skill=task.skill,
                status="failed",
                error=AgentError(
                    code="UPSTREAM_ERROR",
                    message=f"Failed after 2 attempts: {second_error}",
                    retryable=False,
                ),
            )
