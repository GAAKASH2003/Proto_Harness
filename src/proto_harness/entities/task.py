from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, get_args

TaskStatus = Literal["pending", "in_progress", "completed"]
_VALID_STATUSES: frozenset[str] = frozenset(get_args(TaskStatus))

@dataclass(frozen=True,slots=True)
class Task:
    id:str
    content:str
    status:TaskStatus= "pending"

    def __post_init__(self)->None:
        if self.status not in _VALID_STATUSES:
            allowed= ", ".join(sorted(_VALID_STATUSES))
            raise ValueError(f"invalid task status {self.status!r}; expected one of: {allowed}")
        
        if not self.content.strip():
            raise ValueError("Task content must not be empty.")
        
        