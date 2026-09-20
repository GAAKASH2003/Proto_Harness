from __future__ import annotations
import logging
from pydantic_ai import ModelRetry, RunContext
from proto_harness.agent.deps import AgentDeps
from proto_harness.skills.loader import load_skills
from proto_harness.skills.payload import format_skill_payload
logger = logging.getLogger(__name__)
SKILL_TOOL_NAME = "skill"


async def skill(ctx: RunContext[AgentDeps], name: str) -> str:
    """Return the payload of the skill named `name` from the merged catalog.
    If name is not found, raises a ModelRetry informing the model of available skills.
    """
    catalog = load_skills(ctx.deps.cwd)
    found = catalog.get(name)
    if found is None:
        available = ", ".join(sorted(catalog))
        logger.debug("skill tool: unknown skill %r (available: %s)", name, available)
        raise ModelRetry(f"No skill named {name!r}. Available skills: {available}.")
    logger.debug("skill tool returning %r payload (source=%s)", name, found.source)
    return format_skill_payload(found, cwd=ctx.deps.cwd)
