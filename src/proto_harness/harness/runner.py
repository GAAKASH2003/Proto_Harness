from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import Callable

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart

from proto_harness.entities.events import Event
from proto_harness.harness.queue import InteractionQueues
from proto_harness.observability import record_output, root_span

logger = logging.getLogger(__name__)

# The emit function type — called to send events to the TUI.
EventSink = Callable[[Event], None]


def _extract_final_text(messages: list[ModelMessage]) -> str:
    """Extract the final assistant response text from message history."""
    for message in reversed(messages):
        if isinstance(message, ModelResponse):
            parts = [
                part.content
                for part in message.parts
                if isinstance(part, TextPart) and part.content
            ]
            if parts:
                return "\n".join(parts).strip()
    return ""


class Phase(enum.Enum):
    """The single-flight phase machine.

    DISPATCHING exists so the busy state is visible before the first await —
    a racing second submit sees 'not idle' and queues instead of starting a new turn.
    """
    IDLE = "idle"
    DISPATCHING = "dispatching"
    RUNNING = "running"


class Runner:
    """Drives turns one at a time. Single-flight: only one turn runs at a time.

    If the user submits while a turn is running, the message goes into the
    follow_up queue and is picked up automatically when the turn finishes.
    """

    def __init__(
        self,
        *,
        on_event: EventSink,
        on_turn_complete: Callable[[str, float], None] | None = None,
        session_id: str | None = None,
    ) -> None:
        self._on_event = on_event
        self._on_turn_complete = on_turn_complete
        self._session_id = session_id
        self.queues = InteractionQueues()
        self._phase = Phase.IDLE
        self._turn_task: asyncio.Task[None] | None = None
        # Set by the TUI after construction (avoids circular imports).
        self._handler = None

    def set_handler(self, handler: object) -> None:
        """Wire the AgentTurnHandler after construction."""
        self._handler = handler

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def is_busy(self) -> bool:
        return self._turn_task is not None and not self._turn_task.done()

    async def submit(self, text: str) -> None:
        """Accept a user message.

        If idle  → start a new turn immediately.
        If busy  → queue it as a follow-up for after the current turn finishes.
        """
        if self.is_busy:
            # Agent is thinking — park the message, don't interrupt.
            await self.queues.follow_up.put(text)
            logger.debug("Queued follow-up: %r", text)
            return

        # --- Single-flight critical section: set phase BEFORE first await ---
        self._phase = Phase.DISPATCHING
        self._turn_task = asyncio.ensure_future(self._run_turn(text))
        # --------------------------------------------------------------------

    async def _run_turn(self, prompt: str) -> None:
        """Run one full turn, then check for queued follow-ups."""
        self._phase = Phase.RUNNING
        start_time = time.monotonic()
        try:
            with root_span("repl_turn", thread_id=self._session_id, input=prompt) as span:
                await self._handler.run_turn(prompt)
                if span is not None and self._handler is not None:
                    final_text = _extract_final_text(getattr(self._handler, "message_history", []))
                    record_output(span, final_text)
        except Exception:
            logger.exception("Unhandled error in turn")
        finally:
            self._phase = Phase.IDLE
            dur = time.monotonic() - start_time
            if self._on_turn_complete:
                try:
                    self._on_turn_complete(prompt, dur)
                except Exception as exc:
                    logger.debug("on_turn_complete callback failed: %s", exc)

        # After the turn finishes, check if the user sent something while waiting.
        follow_ups = self.queues.drain_follow_up()
        if follow_ups:
            # Combine all queued messages and start a new turn with them.
            combined = "\n".join(follow_ups)
            logger.debug("Starting follow-up turn with %d queued message(s)", len(follow_ups))
            await self.submit(combined)
