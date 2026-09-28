from __future__ import annotations

import logging

from pydantic_ai import Agent

from proto_harness.agent.deps import AgentDeps
from proto_harness.tools.bash import bash
from proto_harness.tools.files import cd, edit, pwd, read, write
from proto_harness.tools.skills import skill
from proto_harness.tools.tasks import todo_write
from proto_harness.tools.orchestration import enter_plan_mode, exit_plan_mode
from proto_harness.tools.agent import agent

logger = logging.getLogger(__name__)

ALL_TOOLS: dict[str, Callable[..., Any]] = {
    "read": read,
    "write": write,
    "edit": edit,
    "cd": cd,
    "pwd": pwd,
    "bash": bash,
    "skill": skill,
    "todo_write": todo_write,
    "enter_plan_mode": enter_plan_mode,
    "exit_plan_mode": exit_plan_mode,
    "agent": agent
}


def register_tools(
    agent: Agent[AgentDeps],
    allowed_tools: tuple[str, ...] | None = None,
) -> None:
    tools_to_register = (
        {name: ALL_TOOLS[name] for name in allowed_tools if name in ALL_TOOLS}
        if allowed_tools is not None
        else ALL_TOOLS
    )
    for name, tool_fn in tools_to_register.items():
        if name == "agent":
            agent.tool(tool_fn, retries=3)
        else:
            agent.tool(tool_fn)
    logger.debug("Registered %d tools: %s", len(tools_to_register), sorted(tools_to_register))