"""The proto_harness CLI entrypoint.

Bootstraps logging, validates provider configuration, and launches the interactive TUI.
"""

from __future__ import annotations

import os
import sys

# Suppress pydantic-ai default ASCII banner
os.environ["PYDANTIC_AI_NO_BANNER"] = "1"

# Ensure UTF-8 encoding and enable Virtual Terminal (VT100 / ANSI) processing on Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # STD_OUTPUT_HANDLE = -11, STD_ERROR_HANDLE = -12
        for handle_id in (-11, -12):
            h = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                kernel32.SetConsoleMode(h, mode.value | 0x0004)

        # Also configure CONOUT$ directly in case handles were redirected
        conout = kernel32.CreateFileW("CONOUT$", 0xC0000000, 3, None, 3, 0, None)
        if conout != -1 and conout != 0:
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(conout, ctypes.byref(mode)):
                kernel32.SetConsoleMode(conout, mode.value | 0x0004)
            kernel32.CloseHandle(conout)
    except Exception:
        pass

from proto_harness.logging import init_logger

# Initialize logging before any project imports
init_logger()

import asyncio
import logging
from pathlib import Path

import click

from proto_harness.config.settings import settings
from proto_harness.permissions.types import PermissionMode
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


@click.command(name="proto")
@click.option(
    "-C",
    "--cwd",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Set working directory for the assistant.",
)
@click.option(
    "-p",
    "--provider",
    type=click.Choice(["gemini", "openrouter"], case_sensitive=False),
    help="Override LLM provider (gemini or openrouter).",
)
@click.option(
    "-m",
    "--model",
    type=str,
    help="Override active LLM model name.",
)
@click.option(
    "-M",
    "--mode",
    type=click.Choice([m.value for m in PermissionMode], case_sensitive=False),
    default=None,
    help="Initial permission mode (default, plan, edit, bypass).",
)
def cli(
    cwd: Path | None,
    provider: str | None,
    model: str | None,
    mode: str | None,
) -> None:
    """Proto Harness: A lightweight terminal coding assistant."""
    error = _provider_config_error()
    if error is not None:
        logger.debug("Provider misconfigured; refusing to start: %s", error)
        click.echo(error, err=True)
        raise click.exceptions.Exit(1)

    if provider:
        settings.llm_provider = provider.lower()  # type: ignore[assignment]
    if model:
        if settings.llm_provider == "openrouter":
            settings.openrouter_model = model
        else:
            settings.gemini_model = model

    target_cwd = (cwd or Path.cwd()).resolve()
    initial_mode = PermissionMode(mode.lower()) if mode else None
    asyncio.run(run_app(cwd=target_cwd, mode=initial_mode))


if __name__ == "__main__":
    cli()
