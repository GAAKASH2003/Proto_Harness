from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

class DecisionChannel:
    def __init__(self) -> None:
        self._pending :asyncio.Future[str]| None = None
    
    @property
    def pending(self) -> bool:
        return self._pending is not None and not self._pending.done()
    
    async def request(self)->str:
        if self.pending: 
            raise RuntimeError("a decision is already pending on this channel")
        
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending = future
        logger.debug("decision channel: awaiting user input")
        try:
            return await future
        finally:
            self._pending = None

    def resolve(self, line: str) -> bool:
        future=self._pending
        if future is None or future.done():
            return False
        logger.debug("decision channel resolved")
        future.set_result(line)
        return True
    
    def cancel(self)->None:
        future=self._pending
        if future is None or future.done():
            return 
        logger.debug("decision channel :cancelling pending request")
        future.cancel()