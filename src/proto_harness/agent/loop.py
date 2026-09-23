from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ToolReturnPart,
    UserPromptPart,
)

from proto_harness.agent.deps import AgentDeps
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage
from proto_harness.config.settings import settings
from proto_harness.context.compaction import (
    CompactOutcome,
    build_summary_message,
    estimate_history_tokens,
    microcompact,
    should_compact,
    split_tail,
    summarize_for_compaction,
)
from proto_harness.entities.events import (
    AgentError,
    AssistantTextDelta,
    ToolCallStarted,
    ToolResult,
    TurnFinished,
    TurnStarted,
    ContextCompacted,
    ContextMicrocompacted
)

logger = logging.getLogger(__name__)


def _leg_input_tokens(messages: list[ModelMessage]) -> int:
    """Input-token occupancy of a leg: read the LAST populated ModelResponse.usage.
    
    Walk backwards through messages to find the first ModelResponse with input_tokens > 0.
    Sum input_tokens + cached prompt tokens.
    """
    for message in reversed(messages):
        if isinstance(message, ModelResponse) and message.usage.input_tokens > 0:
            cached = getattr(message.usage, "cache_read_tokens", 0) or 0
            return message.usage.input_tokens + cached
    return 0


class AgentTurnHandler:
    """Drives agent.iter() for one session, carrying history across turns.

    One instance per REPL session. Call run_turn() for each user message.
    """

    def __init__(
        self,
        agent: Agent[AgentDeps],
        deps: AgentDeps,
        compaction_model: Model | None = None
    ) -> None:
        self._agent = agent
        self._deps = deps
        self._compaction_model = compaction_model or agent.model
        # Grows with every turn — this is how the LLM remembers the conversation.
        self.message_history: list[ModelMessage] = []
        self._announced_tool_calls: set[str] = set()
        self._turn_id = 0
        self._last_input_tokens = 0

    @property
    def last_input_tokens(self) -> int:
        """The input tokens from the last turn, read by the TUI gauge."""
        return self._last_input_tokens

    async def run_turn(self, prompt: str) -> None:
        """Run one full user turn: call the LLM, stream events, update history."""
        self._turn_id += 1
        turn_id = self._turn_id

        self._deps.emit(TurnStarted(turn_id=turn_id, prompt=prompt))

        try:
            await self._run_leg(prompt)
            await self._maybe_auto_compact()
        except Exception as exc:
            logger.exception("Agent error during turn %d", turn_id)
            self._deps.emit(AgentError(message=str(exc)))
        finally:
            self._deps.emit(TurnFinished(turn_id=turn_id))

    async def _run_leg(self, prompt: str) -> None:
        """Run one agent.iter() leg — prompt → LLM response → tool calls → done."""
        async with self._agent.iter(
            prompt,
            deps=self._deps,
            message_history=self.message_history,
        ) as run:
            async for node in run:
                if Agent.is_model_request_node(node):
                    # The LLM is streaming text back to us.
                    await self._stream_model_node(node, run)
                elif Agent.is_call_tools_node(node):
                    # The LLM wants to call one or more tools.
                    await self._stream_tool_node(node, run)

            # Save the full conversation so the next turn has memory.
            self.message_history = run.all_messages()
            self._last_input_tokens = _leg_input_tokens(self.message_history)

    async def _stream_model_node(self, node: object, run: object) -> None:
        """Stream LLM text chunks, emitting AssistantTextDelta for each piece."""
        async with node.stream(run.ctx) as stream:  # type: ignore[attr-defined]
            async for event in stream:
                if isinstance(event, PartStartEvent):
                    # A new text part began — emit its initial content if any.
                    if isinstance(event.part, TextPart) and event.part.content:
                        self._deps.emit(AssistantTextDelta(text=event.part.content))
                elif isinstance(event, PartDeltaEvent):
                    # A new chunk of text arrived — emit it.
                    if isinstance(event.delta, TextPartDelta) and event.delta.content_delta:
                        self._deps.emit(AssistantTextDelta(text=event.delta.content_delta))

    async def _stream_tool_node(self, node: object, run: object) -> None:
        """Stream tool call/result events, emitting ToolCallStarted and ToolResult."""
        async with node.stream(run.ctx) as stream:  # type: ignore[attr-defined]
            async for event in stream:
                if isinstance(event, FunctionToolCallEvent):
                    call = event.part
                    # Only announce each tool call once (avoid duplicates).
                    if call.tool_call_id in self._announced_tool_calls:
                        continue
                    self._announced_tool_calls.add(call.tool_call_id)
                    self._deps.emit(
                        ToolCallStarted(
                            tool_call_id=call.tool_call_id,
                            name=call.tool_name,
                            args=call.args_as_json_str(),
                        )
                    )

                elif isinstance(event, FunctionToolResultEvent):
                    result = event.part
                    if isinstance(result, ToolReturnPart):
                        ok = result.outcome == "success"
                        output = result.model_response_str()
                    elif isinstance(result, RetryPromptPart):
                        ok = False
                        output = result.model_response()
                    else:
                        ok = False
                        output = str(getattr(result, "content", ""))

                    self._deps.emit(
                        ToolResult(
                            tool_call_id=result.tool_call_id,
                            name=result.tool_name or "",
                            output=output,
                            ok=ok,
                        )
                    )


    async def compact(self) -> CompactOutcome:
        """Full LLM compaction: summarize older turns and keep the recent tail."""
        split = split_tail(
            self.message_history, keep_recent_tokens=settings.compaction_keep_recent_tokens
        )
        if split == 0:
            return CompactOutcome.NOTHING_TO_COMPACT

        skeleton = await summarize_for_compaction(
            self.message_history, model=self._compaction_model
        )
        if skeleton is None:
            return CompactOutcome.SUMMARIZER_FAILED

        before_tokens = self._last_input_tokens
        summary_message = build_summary_message(skeleton)
        tail = self.message_history[split:]

        self.message_history = [summary_message, *tail]
        # Re-seed the token estimate immediately so the footer gauge drops
        self._last_input_tokens = estimate_history_tokens(self.message_history)

        self._deps.emit(
            ContextCompacted(before_tokens=before_tokens, kept_messages=len(tail))
        )
        return CompactOutcome.COMPACTED
    
    def _microcompact(self) -> int:
        """Microcompaction: blank old tool-output bodies in memory."""
        new_messages, elided = microcompact(
            self.message_history, keep_recent_tokens=settings.compaction_keep_recent_tokens
        )
        if elided == 0:
            return 0
        self.message_history = new_messages
        self._deps.emit(
            ContextMicrocompacted(elided_count=elided, before_tokens=self._last_input_tokens)
        )
        return elided

    async def _maybe_auto_compact(self) -> None:
        """Check thresholds and run the two-tier compaction cascade at turn end."""
        if not settings.compaction_enabled or self._compaction_model is None:
            return

        usage = RunUsage(input_tokens=self._last_input_tokens)
        window = settings.compaction_context_window_tokens

        full = should_compact(
            usage,
            window=window,
            reserve=settings.compaction_reserve_fraction,
            enabled=settings.compaction_enabled,
        )
        micro = should_compact(
            usage,
            window=window,
            reserve=settings.microcompaction_reserve_fraction,
            enabled=settings.compaction_enabled,
        )

        if full:
            outcome = await self.compact()
            if outcome is not CompactOutcome.COMPACTED:
                logger.info("full compaction trigger fired but did not land: %s", outcome.name)
        elif micro:
            elided = self._microcompact()
            if elided == 0:
                logger.info("microcompaction trigger fired but elided nothing")

    def clear(self) -> None:
        """Reset conversation history."""
        self.message_history = []
        self._last_input_tokens = 0
        self._announced_tool_calls.clear()
