# cli/verifyiq_cli/commands/inspect_cmd.py
"""verifyiq inspect <task_id> — show full artifact dump for a verification request."""

import httpx
import typer
from rich.console import Console

from ..client import VerifyIQClient
from ..display import render_full_report, render_json

console = Console(stderr=True)


def inspect_task(
    task_id: str = typer.Argument(..., help="Task ID from a previous verification request"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Inspect the full artifact dump for a completed verification."""
    client = VerifyIQClient()

    try:
        data = client.get_full(task_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            console.print(f"[bold red]Task not found:[/bold red] {task_id}")
            raise typer.Exit(code=1)
        raise

    if json_output:
        render_json(data)
    else:
        render_full_report(data)
