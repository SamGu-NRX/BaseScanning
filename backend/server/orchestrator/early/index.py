"""Per-capture index of early blobs. Commit and finalize must not scan the Dict."""

from __future__ import annotations

from server.common.lease import with_lease

_INDEX = "early_index:{capture_id}"


def _index_key(capture_id: str) -> str:
    return _INDEX.format(capture_id=capture_id)


def early_keys(state: object, capture_id: str) -> list[str]:
    """Point-read the capture's early keys. A missing index is built once."""

    raw = state.get_kv(_index_key(capture_id))  # type: ignore[attr-defined]
    if isinstance(raw, dict) and isinstance(raw.get("keys"), list):
        return [str(item) for item in raw["keys"]]
    found = [str(key) for key in state.list_blobs(f"early:{capture_id}:")]  # type: ignore[attr-defined]
    state.put_kv(_index_key(capture_id), {"keys": found})  # type: ignore[attr-defined]
    return found


def remember_early(state: object, capture_id: str, key: str) -> None:
    """Append one early blob key. Concurrent commits for one capture keep every key."""

    def add() -> None:
        current = early_keys(state, capture_id)
        if key in current:
            return
        state.put_kv(_index_key(capture_id), {"keys": [*current, key]})  # type: ignore[attr-defined]

    with_lease(state, f"early:{capture_id}", add)
