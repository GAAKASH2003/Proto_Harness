from __future__ import annotations
import importlib.resources
import logging
from pathlib import Path
from typing import Any
import yaml
from proto_harness.entities.agent_def import AgentDef
from proto_harness.frontmatter import split_frontmatter
from proto_harness.permissions.types import PermissionMode

logger = logging.getLogger(__name__)
_BUILTIN_PACKAGE = "proto_harness.agents.builtin"

def parse_agent_file(text: str) -> AgentDef:
    """Parse one catalog Markdown file (frontmatter + body) into an AgentDef.
    Raises ValueError on any schema or structural issue.
    """
    frontmatter, body = split_frontmatter(text)
    meta = yaml.safe_load(frontmatter)
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a YAML mapping of agent fields")
    name = _require_str(meta, "name")
    description = _require_str(meta, "description")
    tools = _require_str_tuple(meta, "tools")
    mode = _parse_mode(meta.get("mode"))
    return AgentDef(
        name=name,
        description=description,
        tools=tools,
        mode=mode,
        prompt=body.strip(),
    )

def load_builtin_agents() -> dict[str, AgentDef]:
    """Read and validate all bundled built-in agents, keyed by name."""
    package = importlib.resources.files(_BUILTIN_PACKAGE)
    agents: dict[str, AgentDef] = {}
    for entry in sorted(package.iterdir(), key=lambda e: e.name):
        if not entry.name.endswith(".md"):
            continue
        text = entry.read_text(encoding="utf-8")
        try:
            agent = parse_agent_file(text)
            agents[agent.name] = agent
        except ValueError as exc:
            raise ValueError(f"invalid built-in agent file {entry.name!r}: {exc}") from exc
    logger.debug("loaded %d built-in agents: %s", len(agents), sorted(agents))
    return agents


def discover_project_agents(cwd: Path) -> dict[str, AgentDef]:
    """Discover optional project-level custom agents in .proto/agents/*.md."""
    agents_dir = cwd / ".proto" / "agents"
    if not agents_dir.is_dir():
        return {}
    agents: dict[str, AgentDef] = {}
    for file in sorted(agents_dir.glob("*.md")):
        text = file.read_text(encoding="utf-8")
        try:
            agent = parse_agent_file(text)
            agents[agent.name] = agent
        except ValueError as exc:
            logger.warning("skipping invalid agent in %s: %exc", file, exc)
    return agents

def load_agents(cwd: Path | None = None) -> dict[str, AgentDef]:
    """Load all agents: built-ins overlaid with any project-level custom agents."""
    agents = load_builtin_agents()
    if cwd is not None:
        project_agents = discover_project_agents(cwd)
        agents.update(project_agents)
    return agents

def load_agent(name: str, cwd: Path | None = None) -> AgentDef:
    """Return the agent named name, or raise ValueError listing available options."""
    all_agents = load_agents(cwd)
    agent = all_agents.get(name)
    if agent is None:
        available = ", ".join(sorted(all_agents))
        raise ValueError(f"no such agent {name!r}; available agents: {available}")
    return agent

def _require_str(meta: dict[str, Any], key: str) -> str:
    value = meta.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key!r} is required and must be a non-empty string")
    return value.strip()

def _require_str_tuple(meta: dict[str, Any], key: str) -> tuple[str, ...]:
    value = meta.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{key!r} is required and must be a non-empty list of tool names")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"all items in {key!r} must be non-empty strings")
        items.append(item.strip())
    return tuple(items)

def _parse_mode(raw: Any) -> PermissionMode:
    if not isinstance(raw, str):
        raise ValueError("'mode' is required and must be a string")
    try:
        return PermissionMode(raw.strip())
    except ValueError:
        valid = ", ".join(m.value for m in PermissionMode)
        raise ValueError(f"unknown mode {raw!r}; valid modes: {valid}") from None








