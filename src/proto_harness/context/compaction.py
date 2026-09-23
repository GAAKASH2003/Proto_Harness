
from __future__ import annotations

import dataclasses
import enum
import logging
from typing import TYPE_CHECKING

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai.usage import RunUsage

logger = logging.getLogger(__name__)


class CompactOutcome(enum.Enum):
    COMPACTED = "compacted"
    NOTHING_TO_COMPACT = "nothing_to_compact"
    SUMMARIZER_FAILED = "summarizer_failed"

_MICRO_PLACEHOLDER = "[tool output elided by microcompaction]"
_CHARS_PER_TOKEN = 4  # Standard ~4 chars per token rule of thumb

_COMPACTION_INSTRUCTIONS = (
    "You compact an in-progress coding session so the assistant can keep working after the older "
    "turns are dropped. Read the transcript below and reply with ONLY this Markdown skeleton — "
    'every heading present, in this exact order, filled from the transcript (write "None" under '
    "any heading that does not apply). No preamble, no extra headings.\n\n"
    "# Conversation summary\n\n"
    "## Goal\n\n"
    "## Constraints & Preferences\n\n"
    "## Progress\n"
    "(Done / In Progress / Blocked)\n\n"
    "## Key Decisions\n\n"
    "## Next Steps\n\n"
    "## Critical Context\n"
)


def reserve_threshold(window: int, reserve: float) -> int:
    """The token level a tier fires at: ``int(window * (1 - reserve))`` (floor, ADR-0006 §3).

    ``window`` must be positive; ``reserve`` (the per-tier fraction kept free) must lie in
    ``[0, 1]``.
    """
    if window <= 0:
        raise ValueError(f"window must be a positive token count, got {window}")
    if not 0 <= reserve <= 1:
        raise ValueError(f"reserve must be within [0, 1], got {reserve}")
    return int(window * (1 - reserve))


def should_compact(usage: RunUsage, *, window: int, reserve: float, enabled: bool) -> bool:
    """Whether a tier should fire this turn — the window-relative predicate shared by both tiers."""
    if not enabled:
        return False
    if usage.input_tokens <= 0:
        return False
    return usage.input_tokens >= reserve_threshold(window, reserve)



def _is_compaction_boundary(message: ModelMessage) -> bool:
    """True if message is a ModelResponse or a clean User ModelRequest without tool results."""
    if isinstance(message, ModelResponse):
        return True
    if isinstance(message, ModelRequest):
        return not any(isinstance(part, (ToolReturnPart, RetryPromptPart)) for part in message.parts)
    return False


def split_tail(messages: list[ModelMessage], *, keep_recent_tokens: int) -> int:
    """Index where the kept recent tail begins, snapped to the nearest safe boundary."""
    if not messages:
        return 0
    total = 0
    cut = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        estimate = _estimate_tokens(messages[index])
        if total + estimate > keep_recent_tokens:
            break
        total += estimate
        cut = index
    if cut == 0:
        return 0  # Everything fits within the recent token budget
    if cut >= len(messages):
        return len(messages)  # Nothing fits — keep only the summary

    # Snap back to the nearest safe Compaction Boundary
    for index in range(cut, -1, -1):
        if _is_compaction_boundary(messages[index]):
            return index
    return 0


def microcompact(
    messages: list[ModelMessage],
    *,
    keep_recent_tokens: int,
    placeholder: str = _MICRO_PLACEHOLDER,
) -> tuple[list[ModelMessage], int]:
    boundary = split_tail(messages, keep_recent_tokens=keep_recent_tokens)

    new_messages: list[ModelMessage] = []
    elided = 0
    for index, message in enumerate(messages):
        if index >= boundary or not isinstance(message, ModelRequest):
            new_messages.append(message)
            continue

        new_parts = list(message.parts)
        changed = False
        for position, part in enumerate(message.parts):
            if not isinstance(part, (ToolReturnPart, RetryPromptPart)):
                continue
            if part.content == placeholder:
                continue  # already elided
            new_parts[position] = dataclasses.replace(part, content=placeholder)
            elided += 1
            changed = True

        new_messages.append(dataclasses.replace(message, parts=new_parts) if changed else message)

    if elided == 0:
        return messages, 0
    return new_messages, elided

def _render_transcript(messages: list[ModelMessage]) -> str:
    """Render a compact plain-text transcript (dropping heavy tool bodies)."""
    lines: list[str] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    text = part.content.strip()
                    if text:
                        lines.append(f"User: {text}")
                elif isinstance(part, ToolReturnPart):
                    lines.append(f"[tool result: {part.tool_name}]")
                elif isinstance(part, RetryPromptPart) and part.tool_name:
                    lines.append(f"[tool retry: {part.tool_name}]")
        elif isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, TextPart) and part.content.strip():
                    lines.append(f"Assistant: {part.content.strip()}")
                elif isinstance(part, ToolCallPart):
                    lines.append(f"[tool call: {part.tool_name}]")
    return "\n".join(lines)

async def summarize_for_compaction(messages: list[ModelMessage], *, model: Model) -> str | None:
    transcript = _render_transcript(messages)
    if not transcript:
        return None

    agent: Agent[None, str] = Agent(model, instructions=_COMPACTION_INSTRUCTIONS)
    try:
        result = await agent.run(transcript)
    except Exception:
        logger.warning("compaction summary call failed; skipping full compaction", exc_info=True)
        return None

    summary = result.output.strip()
    return summary or None

def build_summary_message(skeleton: str) -> ModelRequest:
    """Wraps the summary skeleton in a synthetic head message."""
    framed = f"Summary of the earlier conversation (older turns were compacted):\n\n{skeleton}"
    return ModelRequest(parts=[UserPromptPart(content=framed)])

def estimate_history_tokens(messages: list[ModelMessage]) -> int:
    """Coarse chars // 4 token estimate for the whole history."""
    return sum(_estimate_tokens(message) for message in messages)

def _estimate_tokens(message: ModelMessage) -> int:
    chars = sum(_part_chars(part) for part in message.parts)
    return chars // _CHARS_PER_TOKEN

def _part_chars(part: object) -> int:
    content = getattr(part, "content", None)
    if content is not None:
        return len(content) if isinstance(content, str) else len(str(content))
    args = getattr(part, "args", None)
    if args is not None:
        return len(args) if isinstance(args, str) else len(str(args))
    return 0

    