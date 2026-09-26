from __future__ import annotations

from dataclasses import dataclass,field
from pathlib import Path
from collections.abc import Awaitable, Callable

from proto_harness.entities.events import Event
from proto_harness.entities.permissions import PermissionDecision,PermissionRequest
from proto_harness.permissions.gate import PermissionGate
from proto_harness.entities.task import Task
from proto_harness.entities.agent_def import AgentDef


PermissionResolver = Callable[[PermissionRequest], Awaitable[PermissionDecision]]
UserQuestionResolver = Callable[[str], Awaitable[str]]


@dataclass
class AgentDeps:
    cwd: Path
    emit: Callable[[Event], None]
    gate: PermissionGate
    resolve_permission: PermissionResolver   
    resolve_user_question: UserQuestionResolver | None = None
    task_store: list[Task]=field(default_factory=list)
    active_agent: AgentDef | None = None
