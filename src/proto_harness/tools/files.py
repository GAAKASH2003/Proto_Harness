from __future__ import annotations

import logging
from pathlib import Path

from pydantic_ai import ModelRetry, RunContext

from proto_harness.agent.deps import AgentDeps
from proto_harness.permissions.types import ToolKind
from proto_harness.tools.approval import check_permission

logger = logging.getLogger(__name__)


def _resolve(cwd: Path, raw: str) -> Path:
    base = cwd.resolve()
    target = (base / raw).resolve()
    if base != target and base not in target.parents:
        raise ModelRetry(
            f"Path {raw!r} resolves outside the working directory ({base}). "
            "Use a path relative to the project root, or call cd() to switch the working directory."
        )
    return target


# ---------------------------------------------------------------------------
# cd / pwd
# ---------------------------------------------------------------------------

async def cd(ctx: RunContext[AgentDeps], path: str) -> str:
    """Change the current working directory (cwd) of the session.

    Use this tool whenever you or the user need to navigate to another directory/folder,
    so that subsequent file reads, writes, and bash commands run within that folder.

    Args:
        path: Directory path to switch to (relative to current cwd or absolute path).
    """
    raw = path.strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1]

    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = (ctx.deps.cwd / target).resolve()
    else:
        target = target.resolve()

    if not target.exists():
        raise ModelRetry(f"Directory not found: {path!r}")
    if not target.is_dir():
        raise ModelRetry(f"Path is not a directory: {path!r}")

    ctx.deps.cwd = target
    logger.info("Changed working directory to: %s", target)
    return f"OK — changed working directory to {target}"


async def pwd(ctx: RunContext[AgentDeps]) -> str:
    """Return the current working directory (cwd) of the session."""
    return f"Current working directory: {ctx.deps.cwd.resolve()}"


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

async def read(ctx: RunContext[AgentDeps], path: str) -> str:
    target = _resolve(ctx.deps.cwd, path)
    if not target.exists():
        raise ModelRetry(f"File not found: {path!r}")
    if not target.is_file():
        raise ModelRetry(f"{path!r} is a directory, not a file.")
    try:
        return target.read_text(encoding="utf-8")
    except Exception as exc:
        raise ModelRetry(f"Could not read {path!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

async def write(ctx: RunContext[AgentDeps], path: str, content: str) -> str:
    await check_permission(ctx, "write", f"path={path!r}", ToolKind.FILE_EDIT)
    target = _resolve(ctx.deps.cwd, path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"OK — wrote {len(content)} chars to {path!r}"
    except Exception as exc:
        raise ModelRetry(f"Could not write {path!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------

async def edit(ctx: RunContext[AgentDeps], path: str, old: str, new: str) -> str:
    await check_permission(ctx, "edit", f"path={path!r}", ToolKind.FILE_EDIT)
    target = _resolve(ctx.deps.cwd, path)
    if not target.exists():
        raise ModelRetry(f"File not found: {path!r}. Use write() to create it.")

    try:
        original = target.read_text(encoding="utf-8")
    except Exception as exc:
        raise ModelRetry(f"Could not read {path!r}: {exc}") from exc

    count = original.count(old)
    if count == 0:
        raise ModelRetry(
            f"The string to replace was not found in {path!r}. "
            "Re-read the file to get the exact current content."
        )
    if count > 1:
        raise ModelRetry(
            f"The string to replace appears {count} times in {path!r}. "
            "Make it more specific so there is exactly one match."
        )

    updated = original.replace(old, new, 1)
    try:
        target.write_text(updated, encoding="utf-8")
    except Exception as exc:
        raise ModelRetry(f"Could not write {path!r}: {exc}") from exc

    return f"OK — edited {path!r}"
