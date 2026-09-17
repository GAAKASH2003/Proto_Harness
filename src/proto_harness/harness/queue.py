from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

def _drain(queue: asyncio.Queue[str]) -> list[str]:
    items: list[str] = []
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return items


@dataclass
class InteractionQueues:
    steering: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    follow_up: asyncio.Queue[str] = field(default_factory=asyncio.Queue)

    def drain_steering(self)->list[str]:
        return _drain(self.steering)
    
    def drain_follow_up(self)->list[str]:
        return _drain(self.follow_up)
    
    def clear(self)->None:
        """Discard every queued steering + follow-up message."""
        _drain(self.steering)
        _drain(self.follow_up)