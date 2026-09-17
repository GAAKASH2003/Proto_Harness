from __future__ import annotations

import logging

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from proto_harness.agent.deps import AgentDeps
from proto_harness.config.settings import settings
from proto_harness.tools.registry import register_tools

logger = logging.getLogger(__name__)

# The instructions the LLM receives at the start of every run.
_SYSTEM_PROMPT = (
    "You are agent, a terminal coding assistant that helps a developer in their working "
    "directory. You are concise and precise: answer directly, prefer running the work over "
    "describing it, and never invent file contents or command output you have not seen. "
    "When you do not have a tool for something yet, say so plainly rather than pretending."
)


def _build_model() -> Model:
    """Pick Gemini or OpenRouter based on settings.llm_provider."""
    provider = settings.llm_provider

    if provider == "gemini":
        return GoogleModel(
            settings.gemini_model,
            provider=GoogleProvider(
                api_key=settings.gemini_api_key.get_secret_value(),
            ),
        )

    if provider == "openrouter":
        return OpenAIChatModel(
            settings.openrouter_model,
            provider=OpenRouterProvider(
                api_key=settings.openrouter_api_key.get_secret_value(),
            ),
        )

    raise ValueError(f"Unsupported llm_provider: {provider!r}")


def build_agent() -> Agent[AgentDeps]:
    """Build and return the Pydantic AI agent, ready to run turns."""
    model = _build_model()

    agent: Agent[AgentDeps] = Agent(
        model,
        system_prompt=_SYSTEM_PROMPT,
        deps_type=AgentDeps,
    )

    register_tools(agent)

    logger.debug("Built agent on llm_provider=%s model=%s", settings.llm_provider, settings.active_model)
    return agent
