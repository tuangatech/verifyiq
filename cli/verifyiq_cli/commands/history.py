# cli/verifyiq_cli/commands/history.py
"""verifyiq history — show recent verification requests."""

import typer
from rich.console import Console

from ..client import VerifyIQClient
from ..display import render_history_table, render_json

console = Console()


def show_history(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of recent requests to show"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Show recent verification requests."""
    client = VerifyIQClient()
    items = client.get_history(limit)

    if not items:
        console.print("No verification requests found.")
        return

    if json_output:
        render_json(items)
    else:
        render_history_table(items)
