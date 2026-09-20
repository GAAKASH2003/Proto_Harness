"""Pure mappings from the canonical event union to Rich renderables (append-style).

Output is append-style above a persistent input line — no self-rewriting live region — and
tool calls render on completion as a panel. These functions are pure (one event in, one
renderable out), which makes them exhaustively unit-testable.
"""

from __future__ import annotations

from rich.console import RenderableType
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

from proto_harness.entities import events

# Subtle gray background distinguishing conversation lines (user echo + assistant stream) from
# tool panels/errors.
CONVERSATION_BG = Style(bgcolor="grey15")


def render_event(event: events.Event) -> RenderableType:
    """Map a single canonical event to its Rich renderable."""
    if isinstance(event, events.AssistantTextDelta):
        return _render_assistant_delta(event)
    if isinstance(event, events.ToolCallStarted):
        return _render_tool_call_started(event)
    if isinstance(event, events.ToolResult):
        return _render_tool_result(event)
    if isinstance(event, events.TurnStarted):
        return _render_turn_started(event)
    if isinstance(event, events.TurnFinished):
        return _render_turn_finished(event)
    if isinstance(event, events.AgentError):
        return _render_agent_error(event)
    if isinstance(event, events.PermissionRequested):
        return _render_permission_requested(event)
    raise TypeError(f"render_event got an unsupported event type: {type(event).__name__!r}")


def _render_assistant_delta(event: events.AssistantTextDelta) -> Text:
    """A chunk of the assistant's answer, appended above the prompt."""
    return Text(event.text, style=CONVERSATION_BG)


def _render_tool_call_started(event: events.ToolCallStarted) -> Text:
    """A one-line notice that a tool call started (full panel lands on the result)."""
    if getattr(event, "child_index", None) is None:
        return Text.assemble(("-> ", "dim cyan"), (event.name, "cyan"), (f" {event.args}", "dim"))
    return Text.assemble(
        ("  ", "dim"),
        (f"[child {event.child_index}] ", "dim magenta"),
        ("-> ", "dim cyan"),
        (event.name, "cyan"),
        (f" {event.args}", "dim"),
    )


def _render_tool_result(event: events.ToolResult) -> Panel:
    """A bordered panel summarizing a completed tool call."""
    border = "green" if event.ok else "red"
    body = Text(event.output or "(no output)", style="default" if event.ok else "red")
    title = event.name if event.ok else f"{event.name} (failed)"
    return Panel(body, title=f"[bold]{title}[/bold]", title_align="left", border_style=border)


def _render_turn_started(event: events.TurnStarted) -> Text:
    """The user's message, echoed as 'you \"<message>\"' on the conversation background."""
    return Text.assemble(
        ("you ", "dim cyan"),
        (f'"{event.prompt}"', "default"),
        style=CONVERSATION_BG,
    )


def _render_turn_finished(event: events.TurnFinished) -> Text:
    """A dim marker that a turn ended; notes when it stopped early on abort."""
    if event.aborted:
        return Text("[aborted]", style="dim yellow")
    return Text("[done]", style="dim green")


def _render_agent_error(event: events.AgentError) -> Text:
    """A surfaced error; the turn ends but the REPL stays alive."""
    return Text.assemble(("error: ", "bold red"), (event.message, "red"))


def _render_permission_requested(event: events.PermissionRequested) -> Text:
    """A prompt line announcing the requested tool call approval."""
    return Text.assemble(
        ("permission? ", "bold yellow"),
        (event.tool_name, "yellow"),
        (f" {event.args}", "dim"),
    )
