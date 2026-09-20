from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Awaitable, Callable

from proto_harness.entities.events import Event
from proto_harness.entities.permissions import PermissionDecision,PermissionRequest
from proto_harness.permissions.gate import PermissionGate

PermissionResolver = Callable[[PermissionRequest], Awaitable[PermissionDecision]]

@dataclass
class AgentDeps:
    cwd: Path
    emit: Callable[[Event], None]
    gate: PermissionGate
    resolve_permission: PermissionResolver   
