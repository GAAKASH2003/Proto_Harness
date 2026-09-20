from __future__ import annotations

import importlib.resources
import logging
from pathlib import Path

import yaml
from proto_harness.config.settings import settings
from proto_harness.entities.skill_def import SkillDef
from proto_harness.frontmatter import split_frontmatter

logger = logging.getLogger(__name__)
_BUILTIN_PACKAGE = "proto_harness.skills.builtin"
_BUILTIN_SOURCE = "builtin"
_SKILL_FILE = "SKILL.md"

def parse_skill_file(text:str,source:str,resource_dir:Path | None=None)->SkillDef:
    frontmatter, body = split_frontmatter(text)
    meta = yaml.safe_load(frontmatter)
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a YAML mapping of skill fields")
    return SkillDef(
        name=_require_str(meta, "name"),
        description=_require_str(meta, "description"),
        body=body.strip(),
        source=source.strip(),
        resource_dir=resource_dir,
    )

def load_builtin_skills()->dict[str,SkillDef]:
    package = importlib.resources.files(_BUILTIN_PACKAGE)
    skills: dict[str, SkillDef] = {}
    for entry in sorted(package.iterdir(), key=lambda e: e.name):
        if not entry.is_dir():
            continue  # skip __init__.py and any stray top-level files
        skill_file = entry / _SKILL_FILE
        if not skill_file.is_file():
            logger.debug("skipping built-in dir without %s: %s", _SKILL_FILE, entry.name)
            continue  # also silences __pycache__
        text = skill_file.read_text(encoding="utf-8")
        try:
            skill = parse_skill_file(text, source=_BUILTIN_SOURCE)
        except ValueError as exc:
            raise ValueError(f"invalid built-in skill {entry.name!r}/{_SKILL_FILE}: {exc}") from exc
        skills[skill.name] = skill
    logger.debug("loaded %d built-in skills: %s", len(skills), sorted(skills))
    return skills

def discover_project_skills(cwd: Path) -> dict[str, SkillDef]:
    skills_dir = cwd / settings.skills_dir
    if not skills_dir.is_dir():
        return {}
    skills: dict[str, SkillDef] = {}
    for sub in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if not sub.is_dir():
            continue
        skill_file = sub / _SKILL_FILE
        if not skill_file.is_file():
            logger.warning("skipping skill directory without a %s: %s", _SKILL_FILE, sub)
            continue
        source = str(skill_file.resolve())
        try:
            text = skill_file.read_text(encoding="utf-8")
            resource_dir = sub if any(e.name != _SKILL_FILE for e in sub.iterdir()) else None
            skill = parse_skill_file(text, source=source, resource_dir=resource_dir)
        except (ValueError, OSError, yaml.YAMLError) as exc:
            # ``yaml.YAMLError`` is NOT a ``ValueError`` — catch it explicitly so one broken
            # project skill never crashes the live session (built-ins still raise loudly).
            logger.warning("skipping malformed/unreadable project skill %s: %s", source, exc)
            continue
        if skill.name != sub.name:
            # A mismatch is usually a copy-paste slip — load it, warn loudly (ADR-0004 §3).
            logger.warning(
                "project skill directory %r holds a skill named %r (directory name is cosmetic)",
                sub.name,
                skill.name,
            )
        skills[skill.name] = skill
    logger.debug(
        "discovered %d project skills under %s: %s", len(skills), skills_dir, sorted(skills)
    )
    return skills

def load_skills(cwd: Path) -> dict[str, SkillDef]:
    skills = load_builtin_skills()
    project = discover_project_skills(cwd)
    overridden = sorted(set(skills) & set(project))
    if overridden:
        logger.info("project skills override built-ins by name: %s", overridden)
    skills.update(project)
    return skills

def _require_str(meta: dict[str, object], key: str) -> str:
    """Read a required non-empty string field, stripped; missing/non-string raises ValueError."""
    value = meta.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' is required and must be a non-empty string")
    return value.strip()