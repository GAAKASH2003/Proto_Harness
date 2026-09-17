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
from proto_harness.entities.events import (
    AgentError,
    AssistantTextDelta,
    ToolCallStarted,
    ToolResult,
    TurnFinished,
    TurnStarted,
)

logger = logging.getLogger(__name__)


class AgentTurnHandler:
    """Drives agent.iter() for one session, carrying history across turns.

    One instance per REPL session. Call run_turn() for each user message.
    """

    def __init__(
        self,
        agent: Agent[AgentDeps],
        deps: AgentDeps,
    ) -> None:
        self._agent = agent
        self._deps = deps
        # Grows with every turn — this is how the LLM remembers the conversation.
        self.message_history: list[ModelMessage] = []
        self._announced_tool_calls: set[str] = set()
        self._turn_id = 0

    async def run_turn(self, prompt: str) -> None:
        """Run one full user turn: call the LLM, stream events, update history."""
        self._turn_id += 1
        turn_id = self._turn_id

        self._deps.emit(TurnStarted(turn_id=turn_id, prompt=prompt))

        try:
            await self._run_leg(prompt)
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
