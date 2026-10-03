"""Headless execution runner and durable checkpoint journal for proto_harness.

Drives the agent loop unattended from scripts or CLI commands, outputting clean,
pipeable text to stdout while streaming progress and diagnostics to stderr.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
import time
import uuid

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import Model

from proto_harness.agent.deps import AgentDeps
from proto_harness.agent.factory import build_agent
from proto_harness.agent.loop import AgentTurnHandler
from proto_harness.agents.loader import load_agent
from proto_harness.entities import events
from proto_harness.entities.permissions import PermissionDecision, PermissionRequest
from proto_harness.permissions.gate import PermissionGate
from proto_harness.permissions.types import PermissionMode
from proto_harness.sandbox import (
    apply_sandbox,
    cleanup_sandbox,
    create_sandbox,
    handback_workspace,
    is_git_repo,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HeadlessRunResult:
    """The outcome of an autonomous headless run."""

    output: str
    run_id: str
    agent: str
    mode: str
    prompt: str
    final_response: str
    start_time: str
    end_time: str
    total_tokens: int
    tool_calls_count: int
    duration_seconds: float
    journal_path: Path | None = None
    sandbox_id: str | None = None
    sandbox_branch: str | None = None
    sandbox_applied: bool = False
    diff: str = ""


def _generate_run_id() -> str:
    """Generate a clean timestamped run identifier."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rand = uuid.uuid4().hex[:6]
    return f"run_{ts}_{rand}"


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


def _calculate_tokens(messages: list[ModelMessage]) -> int:
    """Sum input and output tokens across all responses."""
    total = 0
    for message in messages:
        if isinstance(message, ModelResponse):
            total += getattr(message.usage, "input_tokens", 0) or 0
            total += getattr(message.usage, "output_tokens", 0) or 0
    return total


def _count_tool_calls(messages: list[ModelMessage]) -> int:
    """Count the total number of tool calls executed."""
    count = 0
    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, ToolCallPart):
                    count += 1
    return count


def _save_run_journal(
    journal_dir: Path,
    run_id: str,
    payload: dict,
) -> Path | None:
    """Persist the execution trace to disk under .proto_harness/runs/."""
    try:
        journal_dir.mkdir(parents=True, exist_ok=True)
        file_path = journal_dir / f"{run_id}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        return file_path
    except Exception as exc:
        logger.warning("Failed to save run journal for %s: %s", run_id, exc)
        return None


async def run_headless(
    task: str,
    *,
    agent_name: str = "build",
    mode: PermissionMode = PermissionMode.BYPASS,
    cwd: Path | None = None,
    model: Model | None = None,
    quiet: bool = False,
    sandbox: bool = False,
    apply: bool = False,
    discard: bool = False,
) -> HeadlessRunResult:
    """Run a single task to completion headlessly without user intervention.

    - Under BYPASS (the default), tools run without prompts.
    - Diagnostics and live tool invocations stream to stderr.
    - Only final text output is returned (intended for clean stdout piping).
    - When sandbox=True, task runs inside an isolated Git worktree.
    """
    target_cwd = (cwd or Path.cwd()).resolve()
    run_id = _generate_run_id()
    start_time = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()

    sbx = None
    effective_cwd = target_cwd

    if sandbox:
        if not is_git_repo(target_cwd):
            raise RuntimeError(
                f"Cannot run in sandbox mode: '{target_cwd}' is not inside a git repository."
            )
        sbx = create_sandbox(target_cwd)
        effective_cwd = sbx.sandbox_dir
        if not quiet:
            sys.stderr.write(
                f"-> Sandbox active: {sbx.branch_name} ({sbx.sandbox_dir})\n"
            )
            sys.stderr.flush()

    try:
        agent_def = load_agent(agent_name, cwd=effective_cwd)
        agent = build_agent(agent_def=agent_def, model=model)
        gate = PermissionGate(mode=mode)

        recorded_events: list[dict] = []

        def headless_emit(event: events.Event) -> None:
            event_dict = {
                "type": type(event).__name__,
                "time": time.monotonic() - start_time,
            }

            if isinstance(event, events.ToolCallStarted):
                event_dict["tool"] = event.name
                event_dict["args"] = event.args
                if not quiet:
                    child_tag = (
                        f"[child {event.child_index}] "
                        if getattr(event, "child_index", None) is not None
                        else ""
                    )
                    sys.stderr.write(f"\n{child_tag}-> {event.name} {event.args}\n")
                    sys.stderr.flush()

            elif isinstance(event, events.ToolResult):
                event_dict["tool"] = event.name
                event_dict["ok"] = event.ok
                if not quiet and not event.ok:
                    sys.stderr.write(f"  [tool failed: {event.output[:120]}]\n")
                    sys.stderr.flush()

            elif isinstance(event, events.AgentError):
                event_dict["error"] = event.message
                sys.stderr.write(f"\n[error] {event.message}\n")
                sys.stderr.flush()

            recorded_events.append(event_dict)

        async def headless_permission_resolver(
            request: PermissionRequest,
        ) -> PermissionDecision:
            # BYPASS mode never triggers this. In default/plan mode, prompt on tty or deny safely.
            if sys.stdin.isatty():
                sys.stderr.write(
                    f"\n[permission?] {request.tool_name} {request.args}\nAllow? [y/N/a=always]: "
                )
                sys.stderr.flush()
                loop = asyncio.get_running_loop()
                line = await loop.run_in_executor(None, sys.stdin.readline)
                ans = line.strip().lower()
                if ans in ("y", "yes"):
                    return PermissionDecision.allow(mode=mode)
                if ans in ("a", "always"):
                    return PermissionDecision.allow_always(mode=mode)
                return PermissionDecision.deny(
                    mode=mode, reason="Denied by user on stderr prompt."
                )
            return PermissionDecision.deny(
                mode=mode,
                reason="Non-interactive headless run denied mutating tool. Use --mode bypass.",
            )

        deps = AgentDeps(
            cwd=effective_cwd,
            emit=headless_emit,
            gate=gate,
            resolve_permission=headless_permission_resolver,
            resolve_user_question=None,
        )

        handler = AgentTurnHandler(agent=agent, deps=deps)

        if not quiet:
            sys.stderr.write(
                f"Proto Headless [run_id={run_id}] agent={agent_name} mode={mode.value} cwd={effective_cwd}\n"
            )
            sys.stderr.flush()

        await handler.run_turn(task)

        duration_s = round(time.monotonic() - start_time, 2)
        end_time_iso = datetime.now(timezone.utc).isoformat()
        final_output = _extract_final_text(handler.message_history)
        total_tokens = _calculate_tokens(handler.message_history)
        tool_calls_count = _count_tool_calls(handler.message_history)

        diff_str = ""
        applied_ok = False

        if sbx:
            handback = handback_workspace(sbx)
            diff_str = handback.diff

            if apply:
                apply_res = apply_sandbox(sbx, target_cwd=target_cwd)
                applied_ok = apply_res.success
                if not quiet:
                    if applied_ok:
                        sys.stderr.write(f"-> Sandbox changes merged into {target_cwd}\n")
                    else:
                        sys.stderr.write(
                            f"-> Warning: Failed to merge sandbox changes: {apply_res.message}\n"
                        )
                    sys.stderr.flush()

            cleanup_sandbox(sbx, delete_branch=discard)

            if not quiet and not discard and not apply and diff_str:
                sys.stderr.write(
                    f"-> Changes preserved on branch '{sbx.branch_name}'\n"
                )
                sys.stderr.flush()

        journal_dir = target_cwd / ".proto_harness" / "runs"
        journal_payload = {
            "run_id": run_id,
            "task": task,
            "agent": agent_name,
            "mode": mode.value,
            "cwd": str(target_cwd),
            "started_at": started_at,
            "ended_at": end_time_iso,
            "duration_s": duration_s,
            "total_tokens": total_tokens,
            "tool_calls_count": tool_calls_count,
            "output": final_output,
            "events": recorded_events,
            "sandbox_id": sbx.sandbox_id if sbx else None,
            "sandbox_branch": sbx.branch_name if sbx else None,
            "sandbox_applied": applied_ok,
            "diff": diff_str,
        }
        journal_path = _save_run_journal(journal_dir, run_id, journal_payload)

        if not quiet:
            sys.stderr.write(
                f"\n[done] {duration_s}s | {total_tokens} tokens | {tool_calls_count} tools executed\n"
            )
            sys.stderr.flush()

        return HeadlessRunResult(
            output=final_output,
            run_id=run_id,
            agent=agent_name,
            mode=mode.value,
            prompt=task,
            final_response=final_output,
            start_time=started_at,
            end_time=end_time_iso,
            total_tokens=total_tokens,
            tool_calls_count=tool_calls_count,
            duration_seconds=duration_s,
            journal_path=journal_path,
            sandbox_id=sbx.sandbox_id if sbx else None,
            sandbox_branch=sbx.branch_name if sbx else None,
            sandbox_applied=applied_ok,
            diff=diff_str,
        )

    except Exception:
        # In case of unhandled exception, ensure sandbox worktree is cleaned up safely
        if sbx:
            try:
                handback_workspace(sbx)
                cleanup_sandbox(sbx, delete_branch=discard)
            except Exception as clean_exc:
                logger.debug("Failed emergency sandbox cleanup: %s", clean_exc)
        raise
