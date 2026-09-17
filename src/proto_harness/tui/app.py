from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from proto_harness.agent.deps import AgentDeps
from proto_harness.agent.factory import build_agent
from proto_harness.agent.loop import AgentTurnHandler
from proto_harness.entities.events import Event
from proto_harness.harness.runner import Runner
from proto_harness.tui.render import render_event

logger = logging.getLogger(__name__)

# Commands that exit the REPL.
_QUIT_COMMANDS = {"/quit", "/exit", "exit", "quit"}


async def run_app() -> None:
    """The main REPL loop. Called by cli.py.

    Sets up the agent, runner, and TUI, then loops:
      prompt → submit → agent runs → events render → repeat.
    """
    console = Console()

    # ── emit: the bridge from agent events to the terminal ──────────────────
    def emit(event: Event) -> None:
        renderable = render_event(event)
        if renderable is not None:
            console.print(renderable)

    # ── wire all the pieces together ────────────────────────────────────────
    agent = build_agent()

    deps = AgentDeps(
        cwd=Path.cwd(),
        emit=emit,
    )

    handler = AgentTurnHandler(agent=agent, deps=deps)

    runner = Runner(on_event=emit)
    runner.set_handler(handler)

    session: PromptSession[str] = PromptSession()

    console.print("[bold cyan]Agent Harness[/bold cyan] — type a message, /quit to exit\n")

    # ── main loop ───────────────────────────────────────────────────────────
    while True:
        try:
            # patch_stdout keeps the prompt pinned while Rich output scrolls above.
            with patch_stdout():
                user_input = await session.prompt_async("> ")
        except (EOFError, KeyboardInterrupt):
            # Ctrl+D or Ctrl+C — exit cleanly.
            break

        text = user_input.strip()

        if not text:
            continue  # blank line — do nothing

        if text in _QUIT_COMMANDS:
            break

        await runner.submit(text)

        # Wait for the turn to finish before showing the prompt again.
        # This keeps the UX simple: prompt only reappears when agent is done.
        while runner.is_busy:
            await asyncio.sleep(0.05)

    console.print("\n[dim]Sayonara.[/dim]")
