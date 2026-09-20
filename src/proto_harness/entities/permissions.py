from __future__ import annotations

import enum
from dataclasses import dataclass

from proto_harness.permissions.types import PermissionMode, ToolKind


class PermissionOutcome(enum.Enum):
    ALLOW="allow"
    ASK="ask"
    DENY="deny"


@dataclass(frozen=True,slots=True)
class PermissionRequest:
    tool_name:str
    args:str
    kind:ToolKind=ToolKind.OTHER
    subject:str=""
    tool_call_id:str|None=None

    @property
    def read_only(self) -> bool:
        return self.kind is ToolKind.READ_ONLY
    
@dataclass(frozen=True,slots=True)
class PermissionDecision:
    outcome:PermissionOutcome
    mode:PermissionMode=PermissionMode.DEFAULT
    reason:str|None=None

    @classmethod
    def allow(cls, *, mode:PermissionMode=PermissionMode.DEFAULT)->PermissionDecision:
        return cls(outcome=PermissionOutcome.ALLOW,mode=mode)
    
    @classmethod
    def deny(cls, *, mode:PermissionMode=PermissionMode.DEFAULT,reason:str|None=None)->PermissionDecision:
        return cls(outcome=PermissionOutcome.DENY,mode=mode,reason=reason)
    
    @classmethod
    def ask(cls, *, mode:PermissionMode=PermissionMode.DEFAULT)->PermissionDecision:
        return cls(outcome=PermissionOutcome.ASK,mode=mode)