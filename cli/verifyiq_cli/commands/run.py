# cli/verifyiq_cli/commands/run.py
"""verifyiq run <scenario> — submit a scenario and stream results."""

import time

import typer
from rich.console import Console

from ..client import VerifyIQClient
from ..display import render_json, render_sse_event
from ..scenarios import get_scenario

console = Console()


def run_scenario(
    scenario: str = typer.Argument(..., help="Scenario name (e.g. mortgage-intl, rental, auto, hire)"),
    no_watch: bool = typer.Option(False, "--no-watch", help="Skip live SSE streaming; poll for result instead"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON instead of rich panels"),
) -> None:
    """Submit a verification scenario and watch it execute."""
    payload = get_scenario(scenario)

    if scenario == "mortgage-platform":
        console.print("UC-5 (Mortgage Platform) is not yet implemented. Available in Phase 9.")
        raise typer.Exit(code=0)

    client = VerifyIQClient()
    result = client.submit_verification(payload)
    task_id = result["task_id"]
    correlation_id = result["correlation_id"]

    console.print(f"[bold]Submitted:[/bold] task_id={task_id}  correlation_id={correlation_id}")

    if no_watch:
        _poll_until_done(client, task_id)
    else:
        _stream_events(client, task_id)

    if json_output:
        data = client.get_full(task_id)
        render_json(data)
    else:
        status = client.get_status(task_id)
        decision = status.get("decision", "-") or "-"
        console.print(f"\n[bold]Final:[/bold] status={status['status']}  decision={decision}")


def _poll_until_done(client: VerifyIQClient, task_id: str) -> None:
    """Poll GET /verify/{task_id} every second until terminal status."""
    while True:
        status = client.get_status(task_id)
        current = status.get("status", "")
        if current in ("completed", "failed"):
            break
        console.print(f"  status: {current}…", highlight=False)
        time.sleep(1)


def _stream_events(client: VerifyIQClient, task_id: str) -> None:
    """Iterate SSE events and render each one."""
    for event in client.stream_events(task_id):
        render_sse_event(event)
