from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True,slots=True)
class SkillDef:
    name: str
    description: str
    body: str
    source: str
    resource_dir: Path | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("skill name must be a non-empty string")
        if not self.description.strip():
            raise ValueError(f"skill {self.name!r} must have a non-empty description")
        if not self.body.strip():
            raise ValueError(f"skill {self.name!r} must have a non-empty body")
        if not self.source.strip():
            raise ValueError(f"skill {self.name!r} must have a non-empty source")
