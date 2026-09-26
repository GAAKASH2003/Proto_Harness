"""Proto Harness tool system."""

from __future__ import annotations

KNOWN_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "read",
        "write",
        "edit",
        "cd",
        "pwd",
        "bash",
        "skill",
        "todo_write",
        "enter_plan_mode",
        "exit_plan_mode",
    }
)
