from __future__ import annotations

from rich.console import RenderableType
from rich.panel import Panel
from rich.text import Text

from proto_harness.entities.events import (
    AgentError,
    AssistantTextDelta,
    Event,
    ToolCallStarted,
    ToolResult,
    TurnFinished,
    TurnStarted,
)


def render_event(event: Event) -> RenderableType | None:
    """Map one event to a Rich renderable.

    Returns None for events that produce no output (e.g. TurnStarted).
    The caller skips None instead of printing a blank line.
    """
    if isinstance(event, TurnStarted):
        return _render_turn_started(event)

    if isinstance(event, TurnFinished):
        return _render_turn_finished(event)

    if isinstance(event, AssistantTextDelta):
        return _render_assistant_delta(event)

    if isinstance(event, ToolCallStarted):
        return _render_tool_call_started(event)

    if isinstance(event, ToolResult):
        return _render_tool_result(event)

    if isinstance(event, AgentError):
        return _render_agent_error(event)

    return None  # Unknown event — ignore silently


# ---------------------------------------------------------------------------
# Per-event renderers
# ---------------------------------------------------------------------------

def _render_turn_started(event: TurnStarted) -> Text:
    """Echo the user's message back as 'you "<message>"'."""
    return Text.assemble(
        ("you ", "dim cyan"),
        (f'"{event.prompt}"', "default"),
    )


def _render_turn_finished(event: TurnFinished) -> Text:
    """A dim separator marking the end of a turn."""
    if event.aborted:
        return Text("[aborted]", style="dim yellow")
    return Text("")  # just a blank line between turns


def _render_assistant_delta(event: AssistantTextDelta) -> Text:
    """A streaming text chunk from the LLM — no trailing newline so chunks join inline."""
    return Text(event.text, end="")


def _render_tool_call_started(event: ToolCallStarted) -> Text:
    """A dim one-liner showing which tool is being called and with what args."""
    return Text.assemble(
        ("  ▶ ", "dim"),
        (event.name, "bold cyan"),
        ("  ", ""),
        (event.args, "dim"),
    )


def _render_tool_result(event: ToolResult) -> Panel:
    """The tool's output in a bordered panel — green on success, red on failure."""
    border = "green" if event.ok else "red"
    title = f"[bold]{event.name}[/bold]"
    body = Text(event.output or "(no output)")
    return Panel(body, title=title, title_align="left", border_style=border)


def _render_agent_error(event: AgentError) -> Panel:
    """A red panel showing an unexpected agent error."""
    body = Text(event.message, style="red")
    return Panel(body, title="[bold red]Error[/bold red]", title_align="left", border_style="red")

