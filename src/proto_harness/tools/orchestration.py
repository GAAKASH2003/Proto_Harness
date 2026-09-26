from __future__ import annotations
import asyncio
import logging
from pydantic_ai import ModelRetry, RunContext
from proto_harness.agent.deps import AgentDeps
from proto_harness.permissions.types import PermissionMode

logger = logging.getLogger(__name__)
ENTER_PLAN_MODE_TOOL_NAME = "enter_plan_mode"
EXIT_PLAN_MODE_TOOL_NAME = "exit_plan_mode"

_ENTERED_PLAN_MESSAGE = "Entered plan mode: read-only. Present your plan, then call exit_plan_mode."
_PLAN_APPROVED_MESSAGE = "Plan approved — entering edit mode."
_PLAN_DENIED_MESSAGE = "Plan not approved — refine it and call exit_plan_mode again."
_APPROVAL_CUE = "Approve this plan and start editing? [y/N]"
_APPROVE_ANSWERS = frozenset({"y", "yes"})

async def enter_plan_mode(ctx: RunContext[AgentDeps], plan: str = "") -> str:
    ctx.deps.gate.set_mode(PermissionMode.PLAN)
    logger.debug("enter_plan_mode: gate switched to PLAN")
    return _ENTERED_PLAN_MESSAGE

async def exit_plan_mode(ctx: RunContext[AgentDeps], plan: str) -> str:
    if ctx.deps.resolve_user_question is None:
        raise ModelRetry("No interactive user attached to approve plan mode exit.")
    question = f"Plan Proposed:\n{plan}\n\n{_APPROVAL_CUE}"
    try:
        answer = await ctx.deps.resolve_user_question(question)
    except asyncio.CancelledError:
        raise ModelRetry("Plan approval was cancelled. Call exit_plan_mode again when ready.")
    if answer.strip().lower() in _APPROVE_ANSWERS:
        ctx.deps.gate.set_mode(PermissionMode.EDIT)
        logger.debug("exit_plan_mode: approved, switched to EDIT")
        return _PLAN_APPROVED_MESSAGE
    logger.debug("exit_plan_mode: denied, staying in PLAN")
    return _PLAN_DENIED_MESSAGE


