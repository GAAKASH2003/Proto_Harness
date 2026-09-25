from __future__ import annotations
import logging
from pydantic_ai import RunContext
from proto_harness.agent.deps import AgentDeps
from proto_harness.entities import events
from proto_harness.entities.task import Task
from proto_harness.permissions.types import ToolKind
from proto_harness.tools.approval import check_permission

logger = logging.getLogger(__name__)
TODO_WRITE_TOOL_NAME = "todo_write"

_STATUS_MARKERS: dict[str, str] = {
    "pending": "[ ]",
    "in_progress": "[~]",
    "completed": "[x]",
}


def _checklist_lines(tasks: list[Task]) -> tuple[str, ...]:
    """Render each task as a status-marked checklist line (e.g. '[~] implement feature')."""
    return tuple(f"{_STATUS_MARKERS.get(task.status, '[ ]')} {task.content}" for task in tasks)


async def todo_write(ctx: RunContext[AgentDeps], tasks: list[Task]) -> str:
    await check_permission(
        ctx,
        tool_name=TODO_WRITE_TOOL_NAME,
        args=f"{len(tasks)} task(s)",
        kind=ToolKind.READ_ONLY,
    )
    ctx.deps.task_store[:] = tasks
    lines = _checklist_lines(tasks)
    ctx.deps.emit(events.TaskListUpdated(tasks=lines))
    logger.debug("todo_write replaced task store with %d task(s)", len(tasks))
    return f"Updated task list ({len(tasks)} task(s))."

    