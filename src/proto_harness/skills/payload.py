
from __future__ import annotations

import os
from pathlib import Path
from proto_harness.entities.skill_def import SkillDef

OUTPUTS_DIR = ".proto/outputs"  

_OUTPUTS_TRAILER = (
    f"Output default: write NEW files this skill produces under `{OUTPUTS_DIR}/` (create the "
    "directory if missing) — unless the user named a destination path, which always wins. "
    "Edits to existing project files happen in place."
)

def format_skill_payload(skill:SkillDef,*,cwd:Path)->str:
    if skill.resource_dir is None:
        return f"{skill.body}\n\n{_OUTPUTS_TRAILER}"
    
    rel_dir = os.path.relpath(skill.resource_dir, cwd)
    files = _bundled_files(skill.resource_dir)
    if files:
        listing="\n".join(f"- {rel_dir}/{name}" for name in files)
        trailer = (
            f"Bundled files for this skill (all under `{rel_dir}/` — use these EXACT paths):\n"
            f"{listing}\n"
            "Read them with the `read` tool; run `scripts/` files with `bash`."
        )
    else:
        trailer = (
            f"Bundled files for this skill are under `{rel_dir}/` — "
            "read them with the `read` tool, run `scripts/` with `bash`."
        )
    return f"{skill.body}\n\n{trailer}\n\n{_OUTPUTS_TRAILER}"

def _bundled_files(resource_dir: Path) -> list[str]:
    """Every file under the skill's directory — recursive, sorted, POSIX-relative — minus ``SKILL.md``.

    An absent or unreadable directory yields ``[]`` so the caller degrades instead of failing.
    """
    try:
        return sorted(
            path.relative_to(resource_dir).as_posix()
            for path in resource_dir.rglob("*")
            if path.is_file() and path.relative_to(resource_dir).as_posix() != "SKILL.md"
        )   
    except OSError:
        return []

