"""Drop schema-invalid subtrees so later checks only see values that parsed."""

from __future__ import annotations

import copy

from pydantic import ValidationError

from server.contracts.packet import Packet
from server.packet.report import pointer

_OBJECT_TYPES = {"dict_type", "model_type", "model_attributes_type", "is_instance_of"}
_LIST_TYPES = {"list_type", "tuple_type", "set_type"}


def schema_issues(pk: dict) -> list[tuple[str, str, str]]:
    """Pydantic failures as `(code, pointer, message)`. Empty when the packet parses."""
    try:
        Packet.model_validate(pk)
    except ValidationError as exc:
        issues: list[tuple[str, str, str]] = []
        for err in exc.errors():
            loc = err.get("loc") or ()
            message = str(err.get("msg") or "invalid")[:200]
            if err.get("type") == "value_error" and not loc:
                issues.append(("tier_rule", pointer("session", "tier"), message))
            else:
                issues.append(("schema", pointer(*loc) if loc else "/", message))
        return issues
    except RecursionError:
        return [("schema", "/", "packet is nested too deeply")]
    return []


def scrub(pk: dict) -> dict:
    """Copy `pk` with every schema-invalid node replaced by an empty value of the right shape.

    Semantic checks then skip that subtree (brief §4.1) instead of raising on the raw value.
    """
    try:
        cleaned = copy.deepcopy(pk)
    except RecursionError:
        return {}
    try:
        Packet.model_validate(cleaned)
        return cleaned
    except RecursionError:
        return {}
    except ValidationError as exc:
        ranked = sorted(exc.errors(), key=lambda err: len(err.get("loc") or ()), reverse=True)
        for err in ranked:
            _blank(cleaned, tuple(err.get("loc") or ()), str(err.get("type") or ""))
        return cleaned


def _blank(root: object, loc: tuple, kind: str) -> None:
    if not loc:
        return
    parent: object = root
    for key in loc[:-1]:
        if isinstance(parent, dict) and key in parent:
            parent = parent[key]
        elif isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
            parent = parent[key]
        else:
            return
    last = loc[-1]
    expects_object = kind in _OBJECT_TYPES or "model" in kind
    expects_list = kind in _LIST_TYPES
    if isinstance(parent, dict) and isinstance(last, str):
        if expects_list:
            parent[last] = []
        elif expects_object:
            parent[last] = {}
        else:
            parent.pop(last, None)
        return
    if isinstance(parent, list) and isinstance(last, int) and 0 <= last < len(parent):
        # A bad element of a number array (a pose) is dropped with the whole array.
        if expects_object:
            parent[last] = {}
        elif all(not isinstance(item, (dict, list)) for item in parent):
            owner = _owner(root, loc[:-1])
            if owner is not None:
                owner[0].pop(owner[1], None)
        else:
            parent[last] = None


def _owner(root: object, loc: tuple) -> tuple[dict, str] | None:
    if not loc:
        return None
    parent: object = root
    for key in loc[:-1]:
        if isinstance(parent, dict) and key in parent:
            parent = parent[key]
        elif isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
            parent = parent[key]
        else:
            return None
    last = loc[-1]
    if isinstance(parent, dict) and isinstance(last, str):
        return parent, last
    return None
