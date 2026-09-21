from __future__ import annotations
from pathlib import Path
from proto_harness.config.settings import settings
_AGENTS_FILENAME = "AGENTS.md"
_MEMORY_FILENAME = "MEMORY.md"

MEMORY_FILENAMES: tuple[str, ...] = (_AGENTS_FILENAME, _MEMORY_FILENAME)

def harness_memory_path(cwd:Path)->Path:
    return cwd/".proto_harness"/settings.memory_filename

def discover_memory_files(cwd: Path) -> list[Path]:
    agents_cwd_first=[
        level/_AGENTS_FILENAME for level in _ancestors_inclusive(cwd)
        if (level/_AGENTS_FILENAME).is_file()
    ]
    found:list[Path]=list(reversed(agents_cwd_first))

    memory=harness_memory_path(cwd).resolve()
    if memory.is_file():
        found.append(memory)

    return found 

def _ancestors_inclusive(start: Path) -> list[Path]:
    """Return start (resolved) and every parent directory up to the filesystem root."""
    current = start.resolve()
    chain = [current]
    while current.parent != current:
        current = current.parent
        chain.append(current)
    return chain
    