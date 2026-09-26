from __future__ import annotations

import asyncio
import logging
import subprocess

from pydantic_ai import RunContext

from proto_harness.agent.deps import AgentDeps
from proto_harness.config.settings import settings
from proto_harness.permissions.types import ToolKind
from proto_harness.tools.approval import check_permission

logger = logging.getLogger(__name__)

# Cap on the output we return to the LLM — prevents flooding the context window.
_MAX_OUTPUT_BYTES = 50_000


_READ_ONLY_PREFIXES = (
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "git rev-parse",
    "git tag",
    "git remote",
)


def _is_read_only_command(command: str) -> bool:
    """Check if a shell command is a safe read-only query (e.g. git status, git diff)."""
    cmd = command.strip().lower()
    return any(cmd == prefix or cmd.startswith(f"{prefix} ") for prefix in _READ_ONLY_PREFIXES)


async def bash(ctx: RunContext[AgentDeps], command: str) -> str:
    """Run a shell command and return its output (stdout + stderr combined).

    Args:
        command: The shell command to run.
    """
    kind = ToolKind.READ_ONLY if _is_read_only_command(command) else ToolKind.OTHER
    await check_permission(ctx, "bash", f"command={command!r}", kind)
    logger.debug("bash: %r (cwd=%s)", command, ctx.deps.cwd)

    try:
        # Run in a thread so we don't block the async event loop.
        result = await asyncio.to_thread(
            subprocess.run,
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=ctx.deps.cwd,
            timeout=settings.bash_timeout_s if hasattr(settings, "bash_timeout_s") else 120.0,
        )
    except subprocess.TimeoutExpired:
        return f"[timed out after {getattr(settings, 'bash_timeout_s', 120.0)}s]"
    except Exception as exc:
        return f"[error running command: {exc}]"

    # Combine stdout and stderr — just like a real terminal.
    output = result.stdout
    if result.stderr:
        output = output + result.stderr if output else result.stderr

    # Truncate if too large so we don't blow the context window.
    encoded = output.encode("utf-8", errors="replace")
    if len(encoded) > _MAX_OUTPUT_BYTES:
        output = encoded[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        output += f"\n[... output truncated at {_MAX_OUTPUT_BYTES} bytes ...]"

    # Append exit code if the command failed — the LLM needs to know.
    if result.returncode != 0:
        output += f"\n[exit code: {result.returncode}]"

    return output or "[no output]"
