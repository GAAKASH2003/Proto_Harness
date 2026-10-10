from __future__ import annotations

import json
import logging
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model

from proto_harness.agent.deps import AgentDeps
from proto_harness.config.settings import settings
from proto_harness.memory.service import assemble_memory
from proto_harness.skills.catalog import assemble_skills_catalog
from proto_harness.tools.registry import register_tools
from proto_harness.agents.loader import load_agent
from proto_harness.entities.agent_def import AgentDef
from proto_harness.mcp.manager import MCPManager, MCPToolInfo
from proto_harness.permissions.types import ToolKind
from proto_harness.tools.approval import check_permission


logger = logging.getLogger(__name__)

# The instructions the LLM receives at the start of every run.
_SYSTEM_PROMPT = (
    "You are agent, a terminal coding assistant that helps a developer in their working "
    "directory. You are concise and precise: answer directly, prefer running the work over "
    "describing it, and never invent file contents or command output you have not seen. "
    "For any non-trivial multi-step task, lay out the steps with todo_write and keep the "
    "checklist current as you go, marking exactly one item in_progress at a time. "
    "When you do not have a tool for something yet, say so plainly rather than pretending."
)



def _build_model() -> Model:
    """Pick Gemini or OpenRouter based on settings.llm_provider."""
    provider = settings.llm_provider

    if provider == "gemini":
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        return GoogleModel(
            settings.gemini_model,
            provider=GoogleProvider(
                api_key=settings.gemini_api_key.get_secret_value(),
            ),
        )

    if provider == "openrouter":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openrouter import OpenRouterProvider

        return OpenAIChatModel(
            settings.openrouter_model,
            provider=OpenRouterProvider(
                api_key=settings.openrouter_api_key.get_secret_value(),
            ),
        )

    raise ValueError(f"Unsupported llm_provider: {provider!r}")


def _create_mcp_tool_fn(tool_info: MCPToolInfo):
    """Factory creating an async callable for an MCP tool."""
    async def mcp_tool_wrapper(ctx: RunContext[AgentDeps], **kwargs: Any) -> str:
        kind = ToolKind.READ_ONLY if tool_info.is_read_only else ToolKind.OTHER
        args_str = json.dumps(kwargs) if kwargs else "{}"

        await check_permission(
            ctx,
            tool_name=tool_info.namespaced_name,
            args=args_str,
            kind=kind,
        )

        manager = ctx.deps.mcp_manager
        if not manager:
            return f"[Error: MCPManager not available in session]"

        return await manager.call_tool(tool_info.namespaced_name, kwargs)

    return mcp_tool_wrapper


def register_mcp_tools(
    agent: Agent[AgentDeps],
    mcp_manager: MCPManager | None = None,
) -> None:
    """Register all tools discovered from active MCP servers onto the agent."""
    if not mcp_manager:
        return

    tools = mcp_manager.get_all_tools()
    for tool_info in tools:
        tool_fn = _create_mcp_tool_fn(tool_info)
        agent.tool(
            tool_fn,
            name=tool_info.namespaced_name,
            description=tool_info.description,
        )

        registered_tool = agent._function_toolset.tools.get(tool_info.namespaced_name)
        if registered_tool and hasattr(registered_tool, "function_schema") and tool_info.input_schema:
            registered_tool.function_schema.json_schema = tool_info.input_schema

    logger.debug(
        "Registered %d MCP tools: %s",
        len(tools),
        [t.namespaced_name for t in tools],
    )


def build_agent(
    agent_def: AgentDef | None = None,
    model: Model | None = None,
    mcp_manager: MCPManager | None = None,
) -> Agent[AgentDeps]:
    """Build and return the Pydantic AI agent configured for a specific persona."""
    if agent_def is None:
        agent_def = load_agent("build")

    if model is None:
        model = _build_model()

    agent: Agent[AgentDeps] = Agent(
        model,
        deps_type=AgentDeps,
    )

    @agent.system_prompt
    def assemble_instructions(ctx: RunContext[AgentDeps]) -> str:
        base_prompt = agent_def.prompt
        memory = assemble_memory(ctx.deps.cwd)
        catalog = assemble_skills_catalog(ctx.deps.cwd)
        parts = (base_prompt, memory, catalog)
        return "\n\n".join(part for part in parts if part)

    register_tools(agent, allowed_tools=agent_def.tools)
    register_mcp_tools(agent, mcp_manager=mcp_manager)

    logger.debug(
        "Built agent persona=%s tools=%s on llm_provider=%s model=%s",
        agent_def.name,
        agent_def.tools,
        settings.llm_provider,
        settings.active_model,
    )
    return agent
