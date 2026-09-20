"""The tool permission approval guard.

Checks the gate before executing mutating tools (write, edit, bash).
Auto-allows when mode is BYPASS or tool is READ_ONLY;
Raises ModelRetry when denied by PLAN mode or rejected by the user.
"""

from __future__ import annotations

import logging

from pydantic_ai import ModelRetry, RunContext

from proto_harness.agent.deps import AgentDeps
from proto_harness.entities.events import PermissionRequested
from proto_harness.entities.permissions import PermissionOutcome, PermissionRequest
from proto_harness.permissions.types import ToolKind

logger = logging.getLogger(__name__)


async def check_permission(
    ctx: RunContext[AgentDeps],
    tool_name: str,
    args: str,
    kind: ToolKind = ToolKind.OTHER,
) -> None:
    """Check permission before executing a tool.

    If the gate allows, proceeds silently.
    If denied by policy, raises ModelRetry with the refusal reason.
    If the gate asks, emits PermissionRequested, awaits the human decision,
    and proceeds if allowed or raises ModelRetry if denied.
    """
    req = PermissionRequest(tool_name=tool_name, args=args, kind=kind)
    decision = ctx.deps.gate.check(req)

    if decision.outcome is PermissionOutcome.ALLOW:
        logger.debug("Tool %s auto-allowed by gate in mode %s", tool_name, ctx.deps.gate.mode.value)
        return

    if decision.outcome is PermissionOutcome.DENY:
        reason = decision.reason or f"Permission denied for tool '{tool_name}' under {ctx.deps.gate.mode.value} mode."
        logger.debug("Tool %s auto-denied by gate: %s", tool_name, reason)
        raise ModelRetry(reason)

    # Outcome is ASK: emit event for TUI display and await human resolution
    ctx.deps.emit(PermissionRequested(tool_name=tool_name, args=args))
    user_decision = await ctx.deps.resolve_permission(req)

    if user_decision.outcome is not PermissionOutcome.ALLOW:
        reason = user_decision.reason or f"User denied permission to execute tool '{tool_name}'."
        logger.debug("Tool %s denied by user: %s", tool_name, reason)
        raise ModelRetry(reason)

    logger.debug("Tool %s allowed by user", tool_name)
