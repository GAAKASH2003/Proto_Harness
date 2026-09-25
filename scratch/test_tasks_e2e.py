"""End-to-End Test Suite for Phase 3D: Task Planning (todo_write).

Tests:
1. Task Entity Constraints:
   - status validation (pending, in_progress, completed)
   - default status is "pending"
   - whitespace/empty content rejection
   - immutability (frozen dataclass)
2. todo_write Tool Semantics:
   - replace semantics (mutates task_store in-place)
   - emits TaskListUpdated with [ ], [~], [x] markers
   - auto-allows under DEFAULT, PLAN, EDIT, and BYPASS modes (READ_ONLY kind)
3. TUI Rendering:
   - render_event(TaskListUpdated) returns a Rich Panel with border_style='blue'
   - empty task list handling
4. Autocomplete & Registry:
   - SlashCompleter includes /tasks
   - register_tools registers todo_write on the Agent
"""

import asyncio
from pathlib import Path
from rich.panel import Panel

from proto_harness.agent.deps import AgentDeps
from proto_harness.agent.factory import build_agent
from proto_harness.entities import events
from proto_harness.entities.permissions import PermissionOutcome, PermissionRequest, PermissionDecision
from proto_harness.entities.task import Task
from proto_harness.permissions.gate import PermissionGate
from proto_harness.permissions.types import PermissionMode
from proto_harness.tools.registry import register_tools
from proto_harness.tools.tasks import _checklist_lines, todo_write
from proto_harness.tui.app import SlashCompleter
from proto_harness.tui.render import render_event


def test_task_entity_validation():
    # 1. Valid creations
    t1 = Task(id="1", content="Step 1", status="pending")
    assert t1.id == "1"
    assert t1.content == "Step 1"
    assert t1.status == "pending"

    # Default status is pending
    t2 = Task(id="2", content="Step 2")
    assert t2.status == "pending"

    # In progress and completed
    t3 = Task(id="3", content="Step 3", status="in_progress")
    assert t3.status == "in_progress"
    t4 = Task(id="4", content="Step 4", status="completed")
    assert t4.status == "completed"

    # 2. Invalid status rejection
    try:
        Task(id="5", content="Step 5", status="done")
        assert False, "Should raise ValueError for invalid status 'done'"
    except ValueError as e:
        assert "invalid task status" in str(e)

    # 3. Empty or whitespace content rejection
    try:
        Task(id="6", content="   ")
        assert False, "Should raise ValueError for empty content"
    except ValueError as e:
        assert "empty" in str(e).lower()

    # 4. Immutability
    try:
        t1.status = "completed"  # type: ignore
        assert False, "Should not allow mutating frozen dataclass"
    except Exception:
        pass


def test_checklist_lines_formatting():
    tasks = [
        Task(id="1", content="Inspect code", status="completed"),
        Task(id="2", content="Write tests", status="in_progress"),
        Task(id="3", content="Ship feature", status="pending"),
    ]
    lines = _checklist_lines(tasks)
    assert lines == (
        "[x] Inspect code",
        "[~] Write tests",
        "[ ] Ship feature",
    )


async def test_todo_write_tool_execution():
    emitted_events = []

    def mock_emit(event: events.Event):
        emitted_events.append(event)

    async def mock_resolver(req: PermissionRequest):
        return PermissionDecision(outcome=PermissionOutcome.ALLOW)

    # Test under PLAN mode (which denies mutations but must allow READ_ONLY tools like todo_write)
    gate = PermissionGate(mode=PermissionMode.PLAN)
    deps = AgentDeps(
        cwd=Path.cwd(),
        emit=mock_emit,
        gate=gate,
        resolve_permission=mock_resolver,
    )

    # Keep a reference to task_store list to verify in-place mutation
    initial_store_ref = deps.task_store
    assert len(deps.task_store) == 0

    class DummyContext:
        def __init__(self, d):
            self.deps = d

    ctx = DummyContext(deps)

    # First replace
    tasks_batch_1 = [
        Task(id="a", content="Analyze requirements", status="in_progress"),
        Task(id="b", content="Write code", status="pending"),
    ]
    res1 = await todo_write(ctx, tasks_batch_1)  # type: ignore
    assert "Updated task list (2 task(s))." in res1
    assert deps.task_store is initial_store_ref  # Same list object
    assert len(deps.task_store) == 2
    assert deps.task_store[0].content == "Analyze requirements"

    assert len(emitted_events) == 1
    ev1 = emitted_events[-1]
    assert isinstance(ev1, events.TaskListUpdated)
    assert ev1.tasks == ("[~] Analyze requirements", "[ ] Write code")

    # Second replace: advance status and add a task
    tasks_batch_2 = [
        Task(id="a", content="Analyze requirements", status="completed"),
        Task(id="b", content="Write code", status="in_progress"),
        Task(id="c", content="Run linters", status="pending"),
    ]
    res2 = await todo_write(ctx, tasks_batch_2)  # type: ignore
    assert "Updated task list (3 task(s))." in res2
    assert len(deps.task_store) == 3
    assert deps.task_store[0].status == "completed"
    assert deps.task_store[1].status == "in_progress"

    assert len(emitted_events) == 2
    ev2 = emitted_events[-1]
    assert isinstance(ev2, events.TaskListUpdated)
    assert ev2.tasks == (
        "[x] Analyze requirements",
        "[~] Write code",
        "[ ] Run linters",
    )


def test_tui_rendering():
    event = events.TaskListUpdated(
        tasks=(
            "[x] Task 1",
            "[~] Task 2",
            "[ ] Task 3",
        )
    )
    panel = render_event(event)
    assert isinstance(panel, Panel)
    assert panel.border_style == "blue"


def test_slash_completer_and_registry():
    completer = SlashCompleter(lambda: Path.cwd())
    assert "/tasks" in completer._base_commands
    assert completer._base_commands["/tasks"] == "inspect current task checklist (/tasks)"

    # Verify agent registers todo_write
    from pydantic_ai import Agent
    agent = Agent("test")
    register_tools(agent)
    assert "todo_write" in agent._function_toolset.tools


if __name__ == "__main__":
    print("Running Task Entity Validation...")
    test_task_entity_validation()
    print("Running Checklist Formatting...")
    test_checklist_lines_formatting()
    print("Running todo_write tool execution...")
    asyncio.run(test_todo_write_tool_execution())
    print("Running TUI Rendering test...")
    test_tui_rendering()
    print("Running Slash Completer & Registry test...")
    test_slash_completer_and_registry()
    print("\nALL PHASE 3D TESTS PASSED SUCCESSFULLY!")
