from __future__ import annotations

import logging
from pathlib import Path

from proto_harness.skills.loader import load_skills

logger = logging.getLogger(__name__)

_CATALOG_CUE = (
    'Skills you can load on demand — call skill("<name>") to read a skill\'s full '
    "instructions before following it:"
)


def assemble_skills_catalog(cwd: Path) -> str:
    skills = load_skills(cwd)
    if not skills:
        return ""
    ordered = sorted(skills.values(), key=lambda skill: skill.name)
    lines = [
        f"- {' '.join(skill.name.split())} — {' '.join(skill.description.split())}"
        for skill in ordered
    ]
    logger.debug("assembled skills catalog with %d skills: %s", len(lines), sorted(skills))
    return _CATALOG_CUE + "\n" + "\n".join(lines)
