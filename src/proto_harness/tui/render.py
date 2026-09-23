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
_GAUGE_GLYPHS = "○◔◑◕●"


def context_gauge(fraction: float, *, warn_at: float, danger_at: float) -> tuple[str, str]:
    """Map a context-window fill fraction to a (label, color) pair.
    
    Returns plain data (label, color) common to Rich and prompt_toolkit.
    - label: e.g. "○ 12%", "◑ 62%", "● 85%"
    - color: "green", "yellow" (warn), or "red" (danger)
    """
    clamped = min(1.0, max(0.0, fraction))
    glyph = _GAUGE_GLYPHS[round(clamped * 4)]
    label = f"{glyph} {round(clamped * 100)}%"
    if clamped >= danger_at:
        color = "red"
    elif clamped >= warn_at:
        color = "yellow"
    else:
        color = "green"
    return label, color


def _render_context_compacted(event: events.ContextCompacted) -> Text:
    """A dim system line noting full compaction ran."""
    return Text(
        f"Proto - compacted context (~{event.before_tokens} tokens -> "
        f"summary + {event.kept_messages} recent messages).",
        style="dim",
    )

def _render_context_microcompacted(event: events.ContextMicrocompacted) -> Text:
    """A dim system line noting microcompaction blanked old tool outputs."""
    return Text(
        f"Proto - microcompacted context (elided {event.elided_count} old tool output(s), "
        f"~{event.before_tokens} tokens).",
        style="dim",
    )


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
    if isinstance(event, events.ContextCompacted):
        return _render_context_compacted(event)
    if isinstance(event, events.ContextMicrocompacted):
        return _render_context_microcompacted(event)
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
