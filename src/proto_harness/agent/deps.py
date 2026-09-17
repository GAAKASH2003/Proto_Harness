from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from proto_harness.entities.events import Event


@dataclass
class AgentDeps:
    cwd: Path
    emit: Callable[[Event], None]
   
