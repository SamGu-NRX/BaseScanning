"""JSON documents stored in ``StateStore`` blobs. The protocol value is bytes."""

from __future__ import annotations

import json
from typing import Any


def put_json(state: Any, key: str, value: Any) -> None:
    state.put_blob(key, json.dumps(value).encode())


def get_json(state: Any, key: str) -> Any:
    raw = state.get_blob(key)
    if raw is None or raw == b"":
        return None
    if isinstance(raw, bytes):
        return json.loads(raw)
    return raw
