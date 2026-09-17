"""The proto_harness CLI entrypoint.

Bootstraps logging, validates provider configuration, and launches the interactive TUI.
"""

from __future__ import annotations

from proto_harness.logging import init_logger

# Initialize logging before any project imports
init_logger()

import asyncio
import logging

import click

from proto_harness.config.settings import settings
from proto_harness.tui.app import run_app

logger = logging.getLogger(__name__)

_GEMINI_NO_KEY_MESSAGE = (
    "Proto: set GEMINI_API_KEY in your environment or .env to start."
)

_OPENROUTER_NO_KEY_MESSAGE = (
    "Proto: LLM_PROVIDER=openrouter needs OPENROUTER_API_KEY set in your environment or .env."
)


def _provider_config_error() -> str | None:
    """Return a friendly error message if the configured provider lacks required credentials."""
    provider = settings.llm_provider
    if provider == "gemini":
        if not settings.gemini_api_key.get_secret_value():
            return _GEMINI_NO_KEY_MESSAGE
        return None

    if provider == "openrouter":
        if not settings.openrouter_api_key.get_secret_value():
            return _OPENROUTER_NO_KEY_MESSAGE
        return None

    return f"Proto: unknown LLM_PROVIDER {provider!r} (expected 'gemini' or 'openrouter')."


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Proto Harness: A lightweight terminal coding assistant."""
    if ctx.invoked_subcommand is not None:
        return

    error = _provider_config_error()
    if error is not None:
        logger.debug("Provider misconfigured; refusing to start: %s", error)
        click.echo(error, err=True)
        raise click.exceptions.Exit(1)

    asyncio.run(run_app())


if __name__ == "__main__":
    cli()
