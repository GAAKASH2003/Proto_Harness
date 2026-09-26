
from __future__ import annotations

from dataclasses import dataclass
from proto_harness.permissions.types import PermissionMode
from proto_harness.tools import KNOWN_TOOL_NAMES


@dataclass(frozen=True, slots=True)
class AgentDef:
    name: str
    description: str
    tools: tuple[str, ...]
    mode: PermissionMode
    prompt: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("agent name must be a non-empty string")
        if not self.description.strip():
            raise ValueError(f"agent {self.name!r} must have a non-empty description")
        if not self.prompt.strip():
            raise ValueError(f"agent {self.name!r} must have a non-empty prompt")

        unknown = [tool for tool in self.tools if tool not in KNOWN_TOOL_NAMES]
        if unknown:
            known = ", ".join(sorted(KNOWN_TOOL_NAMES))
            raise ValueError(
                f"agent {self.name!r} lists unknown tool(s) {unknown}; known tools: {known}"
            )

