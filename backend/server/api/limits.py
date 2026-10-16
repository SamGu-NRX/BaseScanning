"""In-process token buckets. One API container is the deployment assumption while state is modal.Dict."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime


class Limiter:
    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}

    def allow(
        self, principal: str, bucket: str, capacity: float, per_minute: float, cost: float = 1
    ) -> tuple[bool, int]:
        now = self._clock().timestamp()
        key = (principal, bucket)
        tokens, updated = self._buckets.get(key, (capacity, now))
        elapsed = max(0.0, now - updated)
        tokens = min(capacity, tokens + elapsed * (capacity / per_minute))
        if tokens < cost:
            self._buckets[key] = (tokens, now)
            need = cost - tokens
            retry = max(1, math.ceil(need / (capacity / per_minute)))
            return False, retry
        self._buckets[key] = (tokens - cost, now)
        return True, 0
