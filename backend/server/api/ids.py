"""Id checks for values that become state or object keys.

The pattern is the one published on the contract field (`packetId` /
`amendmentId`). Until that field carries a pattern, only path traversal is
rejected here so a later contract change is the source of the strict shape.
"""

from __future__ import annotations

import re

from server.api.errors import fail

_TRAVERSAL = ("..", "/", "\\", "\x00")


def published_id_pattern(model: type, field_name: str) -> str | None:
    field = model.model_fields.get(field_name)
    if field is None:
        return None
    for meta in field.metadata:
        pattern = getattr(meta, "pattern", None)
        if isinstance(pattern, str) and pattern:
            return pattern
    return None


def require_contract_id(value: str, *, owner: type, field_name: str, pointer: str) -> None:
    pattern = published_id_pattern(owner, field_name)
    if pattern is not None:
        if re.fullmatch(pattern, value) is None:
            raise fail(422, "schema", pointer, "id is not valid")
        return
    if not value or any(token in value for token in _TRAVERSAL):
        raise fail(422, "schema", pointer, "id is not valid")
