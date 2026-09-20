from __future__ import annotations
import logging
from proto_harness.entities.permissions import PermissionDecision, PermissionRequest
from proto_harness.permissions.types import PermissionMode, ToolKind
logger = logging.getLogger(__name__)
_PLAN_DENY_REASON = "Plan mode is read-only — present your plan rather than modifying files or running commands."

class PermissionGate:
    def __init__(self, mode: PermissionMode = PermissionMode.DEFAULT) -> None:
        self._mode = mode

    @property
    def mode(self) -> PermissionMode:
        """The mode the gate evaluates under (``DEFAULT`` at startup)."""
        return self._mode

    def set_mode(self, mode: PermissionMode) -> None:
        """Switch the active mode (the TUI / orchestration tools mutate it mid-session)."""
        logger.debug("gate mode %s -> %s", self._mode.value, mode.value)
        self._mode = mode

    def check(self, request: PermissionRequest | ToolKind) -> PermissionDecision:
        kind = request if isinstance(request, ToolKind) else request.kind
        mode = self._mode
        if mode==PermissionMode.BYPASS:
            return PermissionDecision.allow(mode=mode)
        
        if kind is ToolKind.READ_ONLY:
            return PermissionDecision.allow(mode=mode)
        if mode is PermissionMode.PLAN:
            return PermissionDecision.deny(mode=mode,reason=_PLAN_DENY_REASON)
        
        if mode is PermissionMode.EDIT and kind is ToolKind.FILE_EDIT:
            return PermissionDecision.allow(mode=mode)

        return PermissionDecision.ask(mode=mode)
        