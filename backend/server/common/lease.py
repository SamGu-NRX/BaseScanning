"""A short Dict lease so two containers can update one key without dropping a write."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable


def with_lease(state: object, name: str, fn: Callable[[], None], *, wait_s: float = 3.0, lease_s: float = 10.0) -> None:
    """Run ``fn`` while holding ``lease:{name}``. An abandoned lease can be taken after ``lease_s``."""

    key = f"lease:{name}"
    token = {"id": uuid.uuid4().hex, "at": time.time()}
    deadline = time.monotonic() + wait_s
    while True:
        if state.put_if_absent(key, token):  # type: ignore[attr-defined]
            try:
                return fn()
            finally:
                current = state.get_kv(key)  # type: ignore[attr-defined]
                if isinstance(current, dict) and current.get("id") == token["id"]:
                    state.delete_kv(key)  # type: ignore[attr-defined]
        current = state.get_kv(key)  # type: ignore[attr-defined]
        if isinstance(current, dict) and time.time() - float(current.get("at") or 0) > lease_s:
            state.delete_kv(key)  # type: ignore[attr-defined]
            continue
        if time.monotonic() >= deadline:
            return fn()
        time.sleep(0.01)
