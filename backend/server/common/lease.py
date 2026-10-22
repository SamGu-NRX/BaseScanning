"""A renewable, fenced Dict lease for one shared state transition."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable


class LeaseLost(RuntimeError):
    """The caller tried to write after another owner took the lease."""


class LeaseGuard:
    """Checks that this owner still fences the protected writes."""

    def __init__(self, state: object, key: str, token: dict, heartbeat_key: str) -> None:
        self._state = state
        self._key = key
        self._token = token
        self._heartbeat_key = heartbeat_key

    def assert_held(self) -> None:
        current = self._state.get_kv(self._key)  # type: ignore[attr-defined]
        if not isinstance(current, dict) or current.get("id") != self._token["id"]:
            raise LeaseLost(f"lease {self._key!r} is no longer held")

    def renew(self) -> None:
        self.assert_held()
        # Heartbeats are owner-specific. They never overwrite the lease record,
        # so a late heartbeat cannot extend or replace a successor's lease.
        self._state.put_kv(self._heartbeat_key, {"at": time.time()})  # type: ignore[attr-defined]


def with_lease(
    state: object,
    name: str,
    fn: Callable[..., None],
    *,
    wait_s: float = 3.0,
    lease_s: float = 10.0,
    strict: bool = False,
    pass_guard: bool = False,
) -> None:
    """Run ``fn`` while holding a renewable, owner-fenced lease.

    ``pass_guard`` gives newer callers a :class:`LeaseGuard`; those callers must
    check it immediately before each externally visible write. The owner record
    stays immutable while heartbeats live under an owner-specific key, so an
    expired worker cannot renew or release a successor's lease.
    """

    key = f"lease:{name}"
    token = {"id": uuid.uuid4().hex, "at": time.time()}
    heartbeat_key = f"lease_heartbeat:{name}:{token['id']}"
    deadline = time.monotonic() + wait_s
    while True:
        if state.put_if_absent(key, token):  # type: ignore[attr-defined]
            guard = LeaseGuard(state, key, token, heartbeat_key)
            stop = threading.Event()
            lost = threading.Event()

            def heartbeat(*, _stop=stop, _guard=guard, _lost=lost) -> None:
                interval = max(0.01, min(lease_s / 3.0, 1.0))
                while not _stop.wait(interval):
                    try:
                        _guard.renew()
                    except LeaseLost:
                        _lost.set()
                        return

            # The initial owner timestamp is valid until the first heartbeat.
            state.put_kv(heartbeat_key, {"at": token["at"]})  # type: ignore[attr-defined]
            thread = threading.Thread(target=heartbeat, daemon=True)
            thread.start()
            try:
                if pass_guard:
                    fn(guard)
                else:
                    fn()
                guard.assert_held()
                if lost.is_set():
                    raise LeaseLost(f"lease {name!r} was lost while the callback ran")
            finally:
                stop.set()
                thread.join(timeout=max(0.1, min(lease_s / 3.0, 1.0)))
                current = state.get_kv(key)  # type: ignore[attr-defined]
                if isinstance(current, dict) and current.get("id") == token["id"]:
                    state.delete_kv(key)  # type: ignore[attr-defined]
                state.delete_kv(heartbeat_key)  # type: ignore[attr-defined]
            return
        current = state.get_kv(key)  # type: ignore[attr-defined]
        if isinstance(current, dict):
            owner_id = current.get("id")
            heartbeat = state.get_kv(f"lease_heartbeat:{name}:{owner_id}")  # type: ignore[attr-defined]
            at = heartbeat.get("at") if isinstance(heartbeat, dict) else current.get("at")
            if time.time() - float(at or 0) > lease_s:
                # Only the expired record is removed; a successor's record is
                # protected by its different owner id and release check.
                state.delete_kv(key)  # type: ignore[attr-defined]
                continue
        if time.monotonic() >= deadline:
            if strict:
                raise LeaseBusy(f"lease {name!r} stayed busy for {wait_s:g}s")
            return fn()
        time.sleep(0.01)


class LeaseBusy(RuntimeError):
    """A strict lease could not be acquired within its wait budget."""
