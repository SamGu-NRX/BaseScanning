"""Per-capture in-process lock. Cross-container atomicity is put_if_absent, not this lock."""

from __future__ import annotations

import asyncio
import threading


class CaptureLocks:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = threading.Lock()

    def acquire(self, capture_id: str) -> asyncio.Lock:
        with self._guard:
            lock = self._locks.get(capture_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[capture_id] = lock
            return lock
