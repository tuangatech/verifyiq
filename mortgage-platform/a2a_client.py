# mortgage-platform/a2a_client.py
"""httpx-based A2A client. Knows the A2A wire protocol, nothing about VerifyIQ internals."""

import asyncio
import json
from typing import AsyncGenerator

import httpx
import structlog

logger = structlog.get_logger()


class A2AClient:
    """Minimal A2A client — discovery, task submission, polling, and SSE streaming."""

    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token

    def _headers(self) -> dict[str, str]:
        """Build request headers including bearer auth if configured."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        return headers

    async def discover(self) -> dict:
        """GET /.well-known/agent.json — retrieve the remote agent's card."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self._base_url}/.well-known/agent.json",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def send_task(self, task: dict) -> dict:
        """POST /tasks/send — submit a task to the remote agent."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base_url}/tasks/send",
                json=task,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def get_task(self, task_id: str) -> dict:
        """GET /tasks/{task_id} — poll task status."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self._base_url}/tasks/{task_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def stream_task(self, task_id: str) -> AsyncGenerator[dict, None]:
        """GET /tasks/{task_id}/stream — yield parsed SSE event dicts."""
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "GET",
                f"{self._base_url}/tasks/{task_id}/stream",
                headers=self._headers(),
            ) as response:
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload_str = line[5:].strip()
                    if not payload_str:
                        continue
                    try:
                        yield json.loads(payload_str)
                    except json.JSONDecodeError:
                        continue

    async def send_and_wait(
        self, task: dict, poll_interval: float = 2.0, timeout: float = 90.0
    ) -> dict:
        """Submit a task then poll until terminal status or timeout."""
        ack = await self.send_task(task)
        task_id = ack["task_id"]
        logger.info("task_submitted", task_id=task_id, status=ack.get("status"))

        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            result = await self.get_task(task_id)
            status = result.get("status", "")
            logger.info("poll", task_id=task_id, status=status, elapsed=elapsed)
            if status in ("completed", "failed"):
                return result

        return {"task_id": task_id, "status": "timed_out", "error": "polling timeout"}
