from __future__ import annotations

import asyncio
import enum
import logging
from collections.abc import Callable

from proto_harness.entities.events import Event
from proto_harness.harness.queue import InteractionQueues

logger = logging.getLogger(__name__)

# The emit function type — called to send events to the TUI.
EventSink = Callable[[Event], None]


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
    ) -> None:
        self._on_event = on_event
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
        try:
            await self._handler.run_turn(prompt)
        except Exception:
            logger.exception("Unhandled error in turn")
        finally:
            self._phase = Phase.IDLE

        # After the turn finishes, check if the user sent something while waiting.
        follow_ups = self.queues.drain_follow_up()
        if follow_ups:
            # Combine all queued messages and start a new turn with them.
            combined = "\n".join(follow_ups)
            logger.debug("Starting follow-up turn with %d queued message(s)", len(follow_ups))
            await self.submit(combined)
