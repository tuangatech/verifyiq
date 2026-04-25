# cli/verifyiq_cli/client.py
import json
import os
from typing import Generator

import httpx
import typer
from rich.console import Console

err_console = Console(stderr=True)

DEFAULT_URL = "http://localhost:8000"


class VerifyIQClient:
    """HTTP client wrapper for all Orchestrator communication."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("VERIFYIQ_URL", DEFAULT_URL)
        self._client = httpx.Client(base_url=self.base_url, timeout=10.0)

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Send a request, handle connection errors uniformly."""
        try:
            resp = self._client.request(method, path, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.ConnectError:
            err_console.print(
                f"[bold red]Cannot connect to Orchestrator at {self.base_url}.[/bold red]\n"
                "Is Docker Compose running?"
            )
            raise typer.Exit(code=1)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise
            err_console.print(
                f"[bold red]HTTP {exc.response.status_code}[/bold red] from {exc.request.url}"
            )
            raise typer.Exit(code=1)

    def submit_verification(self, payload: dict) -> dict:
        """POST /verify — submit a verification request."""
        resp = self._request("POST", "/verify", json=payload)
        return resp.json()

    def get_status(self, task_id: str) -> dict:
        """GET /verify/{task_id} — poll for task status."""
        resp = self._request("GET", f"/verify/{task_id}")
        return resp.json()

    def get_full(self, task_id: str) -> dict:
        """GET /verify/{task_id}/full — full artifact dump."""
        resp = self._request("GET", f"/verify/{task_id}/full")
        return resp.json()

    def get_history(self, limit: int = 20) -> list[dict]:
        """GET /verify/history — recent verification requests."""
        resp = self._request("GET", "/verify/history", params={"limit": limit})
        return resp.json()

    def list_agents(self) -> list[dict]:
        """GET /agents — all registered agents."""
        resp = self._request("GET", "/agents")
        return resp.json()

    def stream_events(self, task_id: str) -> Generator[dict, None, None]:
        """GET /verify/{task_id}/stream — yield parsed SSE event dicts."""
        try:
            with self._client.stream(
                "GET", f"/verify/{task_id}/stream", timeout=60.0
            ) as response:
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload_str = line[5:].strip()
                    if not payload_str:
                        continue
                    try:
                        event = json.loads(payload_str)
                    except json.JSONDecodeError:
                        continue
                    yield event
                    event_type = event.get("event_type", "")
                    if event_type in ("completed", "failed"):
                        return
        except httpx.ConnectError:
            err_console.print(
                f"[bold red]Cannot connect to Orchestrator at {self.base_url}.[/bold red]\n"
                "Is Docker Compose running?"
            )
            raise typer.Exit(code=1)
