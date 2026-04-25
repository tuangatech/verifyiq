# cli/verifyiq_cli/display.py
"""Rich rendering functions for CLI output."""

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def render_agents_table(agents: list[dict]) -> None:
    """Render registered agents as a rich table."""
    table = Table(title="Registered Agents")
    table.add_column("Name", style="bold")
    table.add_column("URL")
    table.add_column("Skills")
    table.add_column("Health")
    table.add_column("Avg Latency (ms)", justify="right")
    table.add_column("Last Seen")

    for a in agents:
        skills = a.get("skills", [])
        if isinstance(skills, str):
            try:
                skills = json.loads(skills)
            except (json.JSONDecodeError, TypeError):
                skills = [skills]
        skills_str = ", ".join(skills) if isinstance(skills, list) else str(skills)

        health = a.get("health", "unknown")
        health_style = "green" if health == "healthy" else "red" if health == "unhealthy" else ""
        latency = a.get("avg_latency_ms")
        latency_str = f"{latency:.0f}" if latency is not None else "-"

        table.add_row(
            a.get("name", ""),
            a.get("url", ""),
            skills_str,
            f"[{health_style}]{health}[/{health_style}]" if health_style else health,
            latency_str,
            a.get("last_seen", "-") or "-",
        )

    console.print(table)


def render_history_table(items: list[dict]) -> None:
    """Render verification history as a rich table."""
    table = Table(title="Verification History")
    table.add_column("Task ID", style="bold")
    table.add_column("Subject")
    table.add_column("Use Case")
    table.add_column("Status")
    table.add_column("Decision")
    table.add_column("Created At")

    for item in items:
        task_id = item.get("task_id", "")
        status = item.get("status", "")
        if status == "completed":
            status_str = f"[green]{status}[/green]"
        elif status == "failed":
            status_str = f"[red]{status}[/red]"
        elif status in ("working", "submitted"):
            status_str = f"[yellow]{status}[/yellow]"
        else:
            status_str = status

        table.add_row(
            task_id,
            item.get("subject_name", ""),
            item.get("use_case", ""),
            status_str,
            item.get("decision", "-") or "-",
            item.get("created_at", ""),
        )

    console.print(table)


def render_sse_event(event: dict) -> None:
    """Render a single SSE event as a progress line."""
    event_type = event.get("event_type", "")

    if event_type == "agents_resolved":
        agents = ", ".join(event.get("agents", []))
        console.print(f"[bold]Agents resolved:[/bold] {agents}")

    elif event_type == "agent_started":
        agent = event.get("agent", "")
        console.print(f"  → Starting {agent}...")

    elif event_type == "agent_completed":
        agent = event.get("agent", "")
        console.print(f"  [green]✓ {agent} completed[/green]")

    elif event_type == "agent_failed":
        agent = event.get("agent", "")
        error = event.get("error", "unknown error")
        console.print(f"  [red]✗ {agent} failed: {error}[/red]")

    elif event_type == "agent_skipped":
        agent = event.get("agent", "")
        reason = event.get("reason", "")
        console.print(f"  [dim]○ {agent} skipped: {reason}[/dim]")

    elif event_type == "synthesis_started":
        console.print("  → Running risk synthesis...")

    elif event_type == "synthesis_completed":
        decision = event.get("decision", "")
        confidence = event.get("confidence", "")
        console.print(f"  [green]✓ Synthesis: {decision} (confidence: {confidence})[/green]")

    elif event_type == "completed":
        decision = event.get("decision", "")
        console.print(f"[bold green]Pipeline completed[/bold green] — decision: {decision}")

    elif event_type == "failed":
        error = event.get("error", "")
        console.print(f"[bold red]Pipeline failed[/bold red]: {error}")


def render_full_report(data: dict) -> None:
    """Render the full artifact dump as rich panels."""
    request = data.get("request", {})
    agent_tasks = data.get("agent_tasks", [])

    # Request Summary panel
    req_table = Table(show_header=False, box=None, padding=(0, 2))
    req_table.add_column("Field", style="bold")
    req_table.add_column("Value")
    req_table.add_row("Subject", request.get("subject_name", "-"))
    req_table.add_row("Use Case", request.get("use_case", "-"))
    req_table.add_row("Status", _styled_status(request.get("status", "")))
    req_table.add_row("Decision", request.get("decision", "-") or "-")
    req_table.add_row("Correlation ID", request.get("correlation_id", "-"))
    req_table.add_row("Created", request.get("created_at", "-"))
    req_table.add_row("Completed", request.get("completed_at", "-") or "-")
    console.print(Panel(req_table, title="Request Summary", border_style="blue"))

    # Agent task panels
    synthesis_artifact = None
    for task in agent_tasks:
        agent_name = task.get("agent_name", "unknown")
        status = task.get("status", "")
        started = task.get("started_at", "-")
        ended = task.get("ended_at", "-") or "-"

        task_table = Table(show_header=False, box=None, padding=(0, 2))
        task_table.add_column("Field", style="bold")
        task_table.add_column("Value")
        task_table.add_row("Status", _styled_status(status))
        task_table.add_row("Timing", f"{started} → {ended}")

        artifact = task.get("artifact")
        error = task.get("error")

        if status == "skipped":
            task_table.add_row("Note", "[dim]Not invoked for this use case[/dim]")
        elif error:
            err_msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            task_table.add_row("Error", f"[red]{err_msg}[/red]")
        elif artifact and isinstance(artifact, dict):
            if agent_name == "synthesis":
                synthesis_artifact = artifact
            # Show a compact summary of key artifact fields
            for key, val in artifact.items():
                display_val = _truncate(str(val), 120)
                task_table.add_row(key, display_val)

        border = "green" if status == "completed" else "red" if status == "failed" else "dim"
        console.print(Panel(task_table, title=f"Agent: {agent_name}", border_style=border))

    # Decision panel from synthesis
    if synthesis_artifact:
        dec_table = Table(show_header=False, box=None, padding=(0, 2))
        dec_table.add_column("Field", style="bold")
        dec_table.add_column("Value")
        dec_table.add_row("Decision", f"[bold]{synthesis_artifact.get('decision', '-')}[/bold]")
        dec_table.add_row("Confidence", synthesis_artifact.get("confidence", "-"))
        dec_table.add_row("Risk Score", str(synthesis_artifact.get("risk_score", "-")))

        factors = synthesis_artifact.get("decision_factors", [])
        if factors:
            dec_table.add_row("Decision Factors", ", ".join(factors) if isinstance(factors, list) else str(factors))

        flags = synthesis_artifact.get("risk_flags", [])
        if flags:
            dec_table.add_row("Risk Flags", ", ".join(flags) if isinstance(flags, list) else str(flags))

        summary = synthesis_artifact.get("reasoning_summary", "")
        if summary:
            dec_table.add_row("Reasoning", _truncate(str(summary), 200))

        console.print(Panel(dec_table, title="Decision Summary", border_style="bold green"))


def render_json(data: Any) -> None:
    """Pretty-print raw JSON to stdout."""
    console.print_json(json.dumps(data, default=str))


def _styled_status(status: str) -> str:
    """Return a rich-styled status string."""
    if status == "completed":
        return f"[green]{status}[/green]"
    if status == "failed":
        return f"[red]{status}[/red]"
    if status in ("working", "submitted"):
        return f"[yellow]{status}[/yellow]"
    if status == "skipped":
        return f"[dim]{status}[/dim]"
    return status


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if too long."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
