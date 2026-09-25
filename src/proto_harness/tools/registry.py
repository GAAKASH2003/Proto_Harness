from __future__ import annotations

import logging

from pydantic_ai import Agent

from proto_harness.agent.deps import AgentDeps
from proto_harness.tools.bash import bash
from proto_harness.tools.files import cd, edit, pwd, read, write
from proto_harness.tools.skills import skill
from proto_harness.tools.tasks import todo_write


logger = logging.getLogger(__name__)


def register_tools(agent: Agent[AgentDeps]) -> None:
    """Register all tools onto the agent.

    Called once by build_agent() in factory.py.
    To add a new tool: import it here and call agent.tool().
    """
    agent.tool(read)
    agent.tool(write)
    agent.tool(edit)
    agent.tool(cd)
    agent.tool(pwd)
    agent.tool(bash)
    agent.tool(skill)
    agent.tool(todo_write)
    logger.debug("Registered tools: read, write, edit, cd, pwd, bash")
