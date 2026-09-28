from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai import ModelRetry, RunContext, UsageLimits
from pydantic_ai.messages import FunctionToolCallEvent, ModelResponse, ToolCallPart
from proto_harness.agent.deps import AgentDeps
from proto_harness.config.settings import settings
from proto_harness.entities import events
from proto_harness.permissions.gate import PermissionGate
from proto_harness.permissions.types import PermissionMode, ToolKind
if TYPE_CHECKING:
    from collections.abc import AsyncIterable
    from pydantic_ai import Agent
    from pydantic_ai.agent import AgentRunResult
    from pydantic_ai.messages import AgentStreamEvent

logger = logging.getLogger(__name__)
AGENT_TOOL_NAME = "agent"
_SUBAGENT_PERSONA = "explore"

MAX_FANOUT_PROMPTS = 6
MIN_PROMPT_WORDS = 8
AGENT_TOOL_RETRIES = 3

_RETRY_NUDGE = (
    "\n\n[NOTE: Your previous attempt produced no file:line evidence. "
    "Use your tools (read, etc.) to inspect the actual code first, then report your findings.]"
)
_NO_USABLE_REPORT_NOTE = "The subagent returned no usable report."
_CHILD_FAILED_NOTE = "This subagent failed before producing a report."
_SYNTHESIS_FOOTER = (
    "\n\n---\n**Synthesis Instructions**: Synthesize the subagent reports above into one "
    "coherent, structured answer. Ground your response in the file:line evidence they cited."
)
_SEMAPHORES: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}
_EXPLORE_AGENT: Agent[AgentDeps] | None = None

def _get_semaphore() -> asyncio.Semaphore:
    """Return the concurrency semaphore for the active event loop."""
    loop = asyncio.get_running_loop()
    if loop not in _SEMAPHORES:
        _SEMAPHORES[loop] = asyncio.Semaphore(settings.subagent_max_parallel)
    return _SEMAPHORES[loop]

def _get_explore_agent(cwd: Path) -> Agent[AgentDeps]:
    """Get or build the cached Explore subagent."""
    global _EXPLORE_AGENT
    if _EXPLORE_AGENT is None:
        from proto_harness.agent.factory import build_agent
        from proto_harness.agents.loader import load_agent
        explore_def = load_agent(_SUBAGENT_PERSONA, cwd=cwd)
        _EXPLORE_AGENT = build_agent(agent_def=explore_def)
    return _EXPLORE_AGENT


def _check_substance(prompts: list[str]) -> None:
    """Pre-spawn substance guard: ensure every prompt has sufficient detail."""
    for idx, prompt in enumerate(prompts, start=1):
        words = prompt.strip().split()
        if len(words) < MIN_PROMPT_WORDS:
            raise ModelRetry(
                f"Prompt {idx} ('{prompt}') is too terse ({len(words)} words). "
                f"Each prompt must be at least {MIN_PROMPT_WORDS} words describing the question, "
                "the scope/files to investigate, and the required evidence."
            )

def _label(prompt: str) -> str:
    """Collapse multi-line prompt into a clean single-line header label."""
    return " ".join(prompt.split())

def _read_any_code(result: AgentRunResult[str]) -> bool:
    """Check if the child agent called any tools (i.e. opened and read files)."""
    return any(
        isinstance(part, ToolCallPart)
        for message in result.all_messages()
        if isinstance(message, ModelResponse)
        for part in message.parts
    )

def _truncate_text(text: str, max_bytes: int) -> str:
    """Truncate text to max_bytes cleanly without breaking mid-character."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return f"{truncated}\n[... report truncated at {max_bytes} bytes ...]"

async def _run_attempt(ctx: RunContext[AgentDeps], prompt: str, index: int) -> str | None:
    """Run one attempt of a child Explore subagent."""
    explore_agent = _get_explore_agent(ctx.deps.cwd)
    def child_emit(event: events.Event) -> None:
        if isinstance(event, events.ToolCallStarted):
            ctx.deps.emit(
                events.ToolCallStarted(
                    tool_call_id=event.tool_call_id,
                    name=event.name,
                    args=event.args,
                    child_index=index,
                )
            )
    child_deps = AgentDeps(
        cwd=ctx.deps.cwd,
        emit=child_emit,
        gate=PermissionGate(mode=PermissionMode.BYPASS),
        resolve_permission=lambda r: None,  # type: ignore
        resolve_user_question=None,
        active_agent=explore_agent,  # type: ignore
    )
    async def stream_handler(_ctx: RunContext[AgentDeps], stream: AsyncIterable[AgentStreamEvent]) -> None:
        async for event in stream:
            if isinstance(event, FunctionToolCallEvent):
                call = event.part
                child_emit(
                    events.ToolCallStarted(
                        tool_call_id=call.tool_call_id,
                        name=call.tool_name,
                        args=call.args_as_json_str(),
                        child_index=index,
                    )
                )
    async with _get_semaphore():
        result = await explore_agent.run(
            prompt,
            deps=child_deps,
            usage_limits=UsageLimits(request_limit=settings.subagent_request_limit),
            event_stream_handler=stream_handler,
        )
    text = str(result.output).strip()
    if not text or not _read_any_code(result):
        return None
    return text

async def _spawn_child(ctx: RunContext[AgentDeps], prompt: str, index: int, max_bytes: int) -> str:
    """Run one Explore subagent with validation, rate-limit backoff, and exactly one retry."""
    # Stagger child launches slightly to avoid concurrent burst quota spikes
    if index > 1:
        await asyncio.sleep(0.3 * (index - 1))

    report: str | None = None
    for attempt in range(1, 3):
        try:
            report = await _run_attempt(ctx, prompt, index=index)
            if report is None:
                logger.warning("Subagent %d returned empty/unsupported report; retrying once", index)
                report = await _run_attempt(ctx, prompt + _RETRY_NUDGE, index=index)
            break
        except Exception as exc:
            err_str = str(exc).lower()
            if ("429" in err_str or "quota" in err_str or "rate limit" in err_str) and attempt == 1:
                logger.warning("Subagent %d hit rate limit (%s); backing off 3s before retry", index, exc)
                await asyncio.sleep(3.0 * index)
                continue
            logger.warning("Subagent %d failed: %s", index, exc)
            return _CHILD_FAILED_NOTE
    if report is None:
        return _NO_USABLE_REPORT_NOTE
    return _truncate_text(report, max_bytes)


async def agent(ctx: RunContext[AgentDeps], prompts: list[str]) -> str:
    """Spawn one read-only Explore subagent per prompt, in parallel, and return their reports.
    The subagents run concurrently and read code (read, cd, pwd, skill). Their reports come
    back as ONE labelled document for you to synthesize.
    Write each prompt as a self-contained briefing (at least 8 words) carrying:
      1. the QUESTION to answer;
      2. the SCOPE to search (directories or files);
      3. WHAT THE REPORT MUST CONTAIN (findings with file:line evidence).
    Give each prompt a DISTINCT angle. At most 6 prompts per call.
    """
    if not prompts:
        raise ModelRetry("The agent tool needs at least one exploration prompt in prompts=[...].")
    if len(prompts) > MAX_FANOUT_PROMPTS:
        raise ModelRetry(
            f"You requested {len(prompts)} subagents; limit is {MAX_FANOUT_PROMPTS}. "
            "Consolidate your prompts and try again."
        )
    _check_substance(prompts)
    child_max_bytes = max(1000, settings.subagent_result_max_bytes // len(prompts))
    logger.debug("Fanning out %d explore subagents (%d bytes cap each)", len(prompts), child_max_bytes)
    sections = await asyncio.gather(
        *(
            _spawn_child(ctx, prompt, index=index, max_bytes=child_max_bytes)
            for index, prompt in enumerate(prompts, start=1)
        )
    )
    fold = "\n\n".join(
        f'## Subagent {index} — "{_label(prompt)}"\n\n{section}'
        for index, (prompt, section) in enumerate(zip(prompts, sections, strict=True), start=1)
    )
    return fold + _SYNTHESIS_FOOTER
