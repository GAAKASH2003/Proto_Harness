"""The interactive REPL: a persistent input line + append-style Rich output.

A concurrent prompt_async() wrapped in patch_stdout() keeps the prompt pinned while
output scrolls above it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.style import Style
from rich.text import Text

from proto_harness.agent.deps import AgentDeps
from proto_harness.agent.factory import build_agent
from proto_harness.agent.loop import AgentTurnHandler
from proto_harness.config.settings import settings
from proto_harness.entities import events
from proto_harness.entities.permissions import PermissionDecision, PermissionRequest
from proto_harness.harness.decisions import DecisionChannel
from proto_harness.harness.runner import Runner
from proto_harness.permissions.gate import PermissionGate
from proto_harness.permissions.types import PermissionMode
from proto_harness.memory.extract import extract_on_exit
from proto_harness.memory.files import harness_memory_path
from proto_harness.memory.service import assemble_memory
from proto_harness.skills.loader import load_skills
from proto_harness.skills.payload import format_skill_payload
from proto_harness.tui import render
from prompt_toolkit.formatted_text import HTML
from proto_harness.context.compaction import CompactOutcome

logger = logging.getLogger(__name__)

_QUIT_COMMANDS = {"/quit", "/exit", "exit", "quit"}
_CLEAR_COMMANDS = {"/clear", "/cls", "clear", "cls"}
_PROMPT = "> "
_ASSISTANT_PREFIX = "Agent:: "

def startup_banner(provider: str, model: str, cwd: Path, mode:str) -> str:
    """The startup banner: provider:model and active workspace."""
    return f"Agent - {provider}:{model} - cwd:{cwd} - mode:{mode} - type a line; /quit exits."


def parse_skill_command(line: str) -> tuple[str, str] | None:
    """Split a `/<skill-name> [trailing]` line into (name, trailing), or None."""
    stripped = line.strip()
    if not stripped.startswith("/"):
        return None
    name, _, trailing = stripped[1:].partition(" ")
    name = name.strip().lower()
    if not name:
        return None
    return name, trailing.strip()


class SlashCompleter(Completer):
    """Autocomplete slash commands and skills as the user types `/`."""

    def __init__(self, get_cwd: Callable[[], Path]) -> None:
        self._get_cwd = get_cwd
        self._base_commands = {
            "/mode": "switch or view permission mode (/mode <name>)",
            "/memory": "inspect current workspace memory (/memory)",
            "/compact": "compact conversation history (/compact)",
            "/cd": "change working directory (/cd <path>)",
            "/pwd": "print current working directory",
            "/clear": "clear the terminal screen",
            "/quit": "exit the assistant",
        }

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return

        skills = load_skills(self._get_cwd())
        all_options = dict(self._base_commands)
        for name, s in skills.items():
            all_options[f"/{name}"] = s.description

        for cmd, desc in all_options.items():
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text), display_meta=desc)


def _make_event_sink(console: Console) -> Callable[[events.Event], None]:
    """Build the harness event sink that renders events append-style above the pinned prompt.

    Streamed deltas are LINE-BUFFERED: patch_stdout() redraws output above the live prompt
    and corrupts partial-line writes, so we accumulate and print only COMPLETE lines (split on
    the model's own \\n), flushing the partial tail when the turn ends or any non-streamed
    event interrupts.
    """
    state: dict[str, object] = {
        "need_prefix": False,
        "buffer": "",
        "style": render.CONVERSATION_BG,
    }

    def _emit_line(text: str) -> None:
        style = state["style"]
        if state["need_prefix"] and style == render.CONVERSATION_BG:
            console.print(Text(_ASSISTANT_PREFIX + text, style=style if isinstance(style, (Style, str)) else None))
            state["need_prefix"] = False
        else:
            console.print(Text(text, style=style if isinstance(style, (Style, str)) else None))

    def _flush() -> None:
        buf = str(state["buffer"])
        if buf:
            _emit_line(buf)
            state["buffer"] = ""

    def _stream(text: str, style: Style | str) -> None:
        if state["buffer"] and state["style"] != style:
            _flush()
        state["style"] = style
        state["buffer"] = str(state["buffer"]) + text
        buf = str(state["buffer"])
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            state["buffer"] = buf
            _emit_line(line)

    def on_event(event: events.Event) -> None:
        if isinstance(event, events.AssistantTextDelta):
            _stream(event.text, render.CONVERSATION_BG)
            return

        # Non-streamed event: flush the buffered partial line, arm a new turn's prefix on
        # TurnStarted, then render the event on its own line.
        _flush()
        if isinstance(event, events.TurnStarted):
            state["need_prefix"] = True
            console.print(render.render_event(event))
            console.print(Text("agent thinking...", style="dim cyan"))
            return
        console.print(render.render_event(event))

    return on_event


def _make_permission_resolver(
    decisions: DecisionChannel,
    console: Console,
    gate: PermissionGate,
) -> Callable[[PermissionRequest], Awaitable[PermissionDecision]]:
    """Build the interactive permission resolver backed by the DecisionChannel."""

    async def resolver(request: PermissionRequest) -> PermissionDecision:
        console.print(Text("allow this tool call? [y/N/a=always]", style="bold yellow"))
        try:
            answer = await decisions.request()
        except asyncio.CancelledError:
            return PermissionDecision.deny(mode=gate.mode, reason="User cancelled the approval prompt.")

        stripped = answer.strip().lower()
        if stripped in {"a", "always"}:
            gate.set_mode(PermissionMode.BYPASS)
            console.print(f"Proto - mode switched to: {gate.mode.value}")
            return PermissionDecision.allow(mode=gate.mode)
        if stripped in {"y", "yes", "allow"}:
            return PermissionDecision.allow(mode=gate.mode)
        return PermissionDecision.deny(mode=gate.mode, reason="The user denied permission for this tool call.")

    return resolver


async def run_app(
    cwd: Path | None = None,
    mode: str | PermissionMode | None = None,
) -> None:
    """The main REPL loop. Called by cli.py."""
    console = Console(force_terminal=True)
    emit = _make_event_sink(console)

    active_cwd = (cwd or Path.cwd()).resolve()

    initial_mode = PermissionMode.DEFAULT
    if mode is not None:
        initial_mode = PermissionMode(mode.lower()) if isinstance(mode, str) else mode

    gate = PermissionGate(mode=initial_mode)
    decisions = DecisionChannel()
    resolve_permission = _make_permission_resolver(decisions, console, gate)

    agent = build_agent()
    deps = AgentDeps(
        cwd=active_cwd,
        emit=emit,
        gate=gate,
        resolve_permission=resolve_permission,
    )
    handler = AgentTurnHandler(agent=agent, deps=deps)

    runner = Runner(on_event=emit)
    runner.set_handler(handler)

    session: PromptSession[str] = PromptSession(completer=SlashCompleter(lambda: deps.cwd))

    console.print(startup_banner(settings.llm_provider, settings.active_model, deps.cwd, gate.mode.value))

    def _get_prompt() -> HTML:
        if decisions.pending:
            return HTML('<style fg="yellow">allow tool call? [y/N/a]</style> > ')
        if runner.is_busy:
            return HTML('<style fg="cyan">working… (or type to queue)</style> > ')
        window = settings.compaction_context_window_tokens
        fraction = handler.last_input_tokens / window if window > 0 else 0.0
        warn_at = 1 - settings.microcompaction_reserve_fraction
        danger_at = 1 - settings.compaction_reserve_fraction
        label, color = render.context_gauge(fraction, warn_at=warn_at, danger_at=danger_at)
        tokens_str = f" ({handler.last_input_tokens:,} tok)" if handler.last_input_tokens > 0 else ""
        return HTML(f'<style fg="{color}">{label}{tokens_str}</style> > ')


    with patch_stdout(raw=True):
        while True:
            try:
                user_input = await session.prompt_async(_get_prompt)
            except (EOFError, KeyboardInterrupt):
                if decisions.pending:
                    decisions.cancel()
                break

            text = user_input.strip()
            if not text:
                continue

            # If a tool approval is pending on the DecisionChannel, route the line directly to it!
            if decisions.pending:
                decisions.resolve(text)
                continue

            lower = text.lower()

            if lower in _QUIT_COMMANDS:
                break

            if lower in {"/pwd", "/cwd", "pwd"}:
                console.print(f"Proto - cwd: {deps.cwd}")
                continue

            if lower in {"/memory", "memory"}:
                memory_block = assemble_memory(deps.cwd)
                if memory_block.strip():
                    console.print(f"[bold cyan]Active Memory for {deps.cwd}:[/bold cyan]\n{memory_block.strip()}")
                else:
                    console.print(f"Proto - no memory recorded yet for {deps.cwd} (no AGENTS.md or .proto_harness/MEMORY.md).")
                continue

            if lower in {"/compact", "compact"}:
                console.print("Proto - compacting context...")
                outcome = await handler.compact()
                if outcome == CompactOutcome.NOTHING_TO_COMPACT:
                    console.print("Proto - nothing to compact (history fits within recent token budget).")
                elif outcome == CompactOutcome.SUMMARIZER_FAILED:
                    console.print("[red]Proto - compaction failed: summarizer call failed or returned empty.[/red]")
                continue
            
            if lower in _CLEAR_COMMANDS:
                console.clear()
                handler.clear()
                console.print(startup_banner(settings.llm_provider, settings.active_model, deps.cwd, gate.mode.value))
                continue


            if lower.startswith("/mode ") or lower == "/mode":
                parts = text.split(" ", 1)
                mode_name = parts[1].strip().lower() if len(parts) > 1 else ""
                if not mode_name:
                    console.print(f"Proto - current mode: {gate.mode.value} (options: default, plan, edit, bypass)")
                else:
                    try:
                        gate.set_mode(PermissionMode(mode_name))
                        console.print(f"Proto - mode switched to: {gate.mode.value}")
                    except ValueError:
                        console.print(f"Proto - unknown mode: {mode_name} (expected: default, plan, edit, bypass)")
                continue

            if lower.startswith("/cd ") or lower.startswith("cd "):
                parts = text.split(" ", 1)
                raw_path = parts[1].strip() if len(parts) > 1 else ""
                if (raw_path.startswith('"') and raw_path.endswith('"')) or (
                    raw_path.startswith("'") and raw_path.endswith("'")
                ):
                    raw_path = raw_path[1:-1]

                if not raw_path:
                    console.print(f"Proto - cwd: {deps.cwd}")
                    continue

                target = Path(raw_path).expanduser()
                if not target.is_absolute():
                    target = (deps.cwd / target).resolve()
                else:
                    target = target.resolve()

                if target.is_dir():
                    deps.cwd = target
                    console.print(f"Proto - cwd: {deps.cwd}")
                else:
                    console.print(f"Proto - directory not found: {raw_path}")
                continue

            # Check for skill execution (e.g. /commit or /code-review)
            skill_cmd = parse_skill_command(text)
            if skill_cmd is not None:
                skill_name, trailing = skill_cmd
                catalog = load_skills(deps.cwd)
                found = catalog.get(skill_name)
                if found is not None:
                    payload = format_skill_payload(found, cwd=deps.cwd)
                    turn_input = f"{payload}\n\n{trailing}" if trailing else payload
                    await runner.submit(turn_input)
                    continue
                else:
                    available = ", ".join(sorted(catalog))
                    console.print(f"Proto - unknown command '/{skill_name}'; available skills: {available}")
                    continue

            if runner.is_busy:
                console.print("[dim cyan]Proto - turn in progress; queued message as follow-up.[/dim cyan]")
            await runner.submit(text)

    # Wait for the active turn to finish before exit
    while runner.is_busy:
        await asyncio.sleep(0.05)

    # On-exit memory write-back (non-fatal) to the active working directory
    await extract_on_exit(handler.message_history, deps.cwd)
