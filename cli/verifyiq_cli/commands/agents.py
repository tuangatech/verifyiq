# cli/verifyiq_cli/commands/agents.py
"""verifyiq agents — list registered agents."""

import typer
from rich.console import Console

from ..client import VerifyIQClient
from ..display import render_agents_table, render_json

console = Console()


def list_agents(
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Show all agents registered with the Agent Registry."""
    client = VerifyIQClient()
    agents = client.list_agents()

    if not agents:
        console.print("No agents registered. Is the stack running?")
        return

    if json_output:
        render_json(agents)
    else:
        render_agents_table(agents)
