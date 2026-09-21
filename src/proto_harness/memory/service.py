from __future__ import annotations
import logging
from pathlib import Path
from typing import Literal
from proto_harness.config.settings import settings
from proto_harness.memory.files import MEMORY_FILENAMES, discover_memory_files

logger = logging.getLogger(__name__)

_CAPPED_FILENAME = "MEMORY.md"
assert _CAPPED_FILENAME in MEMORY_FILENAMES

def assemble_memory(cwd: Path) -> str:
    """Read discovered memory files and return the prompt block to inject."""
    blocks: list[str] = []
    for path in discover_memory_files(cwd):
        content = _read_text(path)
        if content is None:
            continue
        if path.name == _CAPPED_FILENAME:
            content = _cap(content)
        blocks.append(f"# From {path}\n{content}")
    return "\n\n".join(blocks)

def _read_text(path: Path) -> str | None:
    """Read path as UTF-8, returning None if unreadable."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("skipping unreadable memory file %s: %s", path, exc)
        return None

def _cap(content: str) -> str:
    """Clip content to configured line and byte budgets."""
    max_lines = settings.memory_max_lines
    max_bytes = settings.memory_max_bytes
    lines = content.splitlines()
    fits_lines = len(lines) <= max_lines
    fits_bytes = len(content.encode("utf-8")) <= max_bytes
    if fits_lines and fits_bytes:
        return content
    kept = clip_lines_to_budget(lines, max_lines=max_lines, max_bytes=max_bytes, keep="head")
    note = (
        f"\n\n[memory truncated to {max_lines} lines / {max_bytes} bytes; "
        f"the file continues beyond this point]"
    )
    return kept + note

    
def clip_lines_to_budget(
    lines: list[str], *, max_lines: int, max_bytes: int, keep: Literal["head", "tail"]
) -> str:
    """Clip lines to a line AND byte budget, keeping whole lines from one end."""
    if keep == "head":
        kept = lines[:max_lines]
        while len(kept) > 1 and len("\n".join(kept).encode("utf-8")) > max_bytes:
            kept = kept[:-1]
    else:
        kept = lines[-max_lines:] if max_lines > 0 else lines[-1:]
        while len(kept) > 1 and len("\n".join(kept).encode("utf-8")) > max_bytes:
            kept = kept[1:]
    return "\n".join(kept)