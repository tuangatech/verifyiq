# cli/verifyiq_cli/main.py
"""Typer app entry point — registers all subcommands."""

import typer

from .commands import agents, history, inspect_cmd, run

app = typer.Typer(
    name="verifyiq",
    help="VerifyIQ — Multi-Agent Verification Platform CLI",
    no_args_is_help=True,
)

# Register subcommands using the decorator-call pattern instead of @app.command()
# because the handler functions are defined in separate modules.
app.command(name="run")(run.run_scenario)
app.command(name="agents")(agents.list_agents)
app.command(name="history")(history.show_history)
app.command(name="inspect")(inspect_cmd.inspect_task)
