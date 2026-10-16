"""Per-capture in-process lock. Cross-container atomicity is put_if_absent, not this lock."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager


class CaptureLocks:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._finishing: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def acquire(self, capture_id: str) -> asyncio.Lock:
        with self._guard:
            lock = self._locks.get(capture_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[capture_id] = lock
            return lock

    def finish_sync(self, capture_id: str) -> threading.Lock:
        """Serialize background publication with DELETE in this API container."""
        with self._guard:
            return self._finishing.setdefault(capture_id, threading.Lock())

    @asynccontextmanager
    async def finish_async(self, capture_id: str):
        lock = self.finish_sync(capture_id)
        # Never leave a worker thread acquiring a lock after its HTTP request was
        # cancelled. A nonblocking attempt also keeps other captures responsive.
        while not lock.acquire(blocking=False):
            await asyncio.sleep(0.01)
        try:
            yield
        finally:
            lock.release()
