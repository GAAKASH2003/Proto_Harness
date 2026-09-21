from __future__ import annotations
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from proto_harness.agent.factory import _build_model
from proto_harness.config.settings import settings
from proto_harness.memory.files import harness_memory_path
from proto_harness.memory.service import clip_lines_to_budget

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

logger = logging.getLogger(__name__)

_SUMMARIZE_INSTRUCTIONS = (
    "You summarize a finished coding session for a developer's project memory. Read the "
    "transcript below and reply with ONE plain sentence capturing what was worked on or decided "
    "— no preamble, no bullet points, no markdown, just the sentence."
)


def _utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def _render_transcript(messages: list[ModelMessage]) -> str:
    """Turn conversation history into a simple transcript string."""
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    lines.append(f"User: {part.content}")
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart) and part.content.strip():
                    lines.append(f"Assistant: {part.content.strip()}")
    return "\n".join(lines)


async def summarize_session(messages: list[ModelMessage]) -> str | None:
    """Summarize a session into one sentence with a single cheap LLM call."""
    transcript = _render_transcript(messages)
    if not transcript:
        return None
    try:
        model = _build_model()
        agent: Agent[None, str] = Agent(model, system_prompt=_SUMMARIZE_INSTRUCTIONS)
        result = await agent.run(transcript)
        summary = result.output.strip()
        return summary or None
    except Exception:
        logger.warning("session summary call failed; skipping memory write-back", exc_info=True)
        return None
    
def append_session_summary(cwd: Path, summary: str, *, now: datetime) -> None:
    """Append a dated '- YYYY-MM-DD: ...' bullet to .proto_harness/MEMORY.md and trim to budget."""
    if now.tzinfo is None:
        raise ValueError("now must be a timezone-aware (UTC) datetime")
    memory = harness_memory_path(cwd)
    memory.parent.mkdir(parents=True, exist_ok=True)
    date_str = now.strftime("%Y-%m-%d")
    bullet = f"- {date_str}: {summary.strip()}"
    existing = memory.read_text(encoding="utf-8") if memory.is_file() else ""
    lines = [line for line in existing.splitlines() if line.strip()]
    lines.append(bullet)
    trimmed = clip_lines_to_budget(
        lines,
        max_lines=settings.memory_max_lines,
        max_bytes=settings.memory_max_bytes,
        keep="tail",
    )
    memory.write_text(trimmed + "\n", encoding="utf-8")


async def extract_on_exit(messages: list[ModelMessage], cwd: Path) -> None:
    """Non-fatal orchestrator called on session exit."""
    try:
        summary = await summarize_session(messages)
        if summary:
            append_session_summary(cwd, summary, now=_utc_now())
            logger.info("recorded session memory: %s", summary)
    except Exception as exc:
        logger.warning("on-exit memory extraction failed (non-fatal): %s", exc)

