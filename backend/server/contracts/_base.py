"""Shared pydantic base classes, wire types, and the canonical JSON dump."""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, WithJsonSchema

_ISO_TIME = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$")
_REL_PATH = re.compile(r"^(?!/)(?!.*\.\./).+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _number(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("expected a number")
    return value


def _integer(value: Any) -> int | float:
    if isinstance(value, bool):
        raise ValueError("expected an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return value
    raise ValueError("expected an integer")


def _fixed_numbers(count: int):
    def check(value: Any) -> list[int | float]:
        if not isinstance(value, list) or len(value) != count:
            raise ValueError(f"expected {count} numbers")
        return [_number(item) for item in value]

    return BeforeValidator(check)


JsonNum = Annotated[int | float, BeforeValidator(_number)]
JsonInt = Annotated[int | float, BeforeValidator(_integer)]
# Field length is what model_json_schema emits; the validator keeps the same check on input.
Vec3 = Annotated[list[JsonNum], Field(min_length=3, max_length=3), _fixed_numbers(3)]
Point3 = Vec3
Point2 = Annotated[list[JsonNum], Field(min_length=2, max_length=2), _fixed_numbers(2)]
Mat4 = Annotated[list[JsonNum], Field(min_length=16, max_length=16), _fixed_numbers(16)]
Intrinsics = Annotated[list[JsonNum], Field(min_length=4, max_length=4), _fixed_numbers(4)]
Box4 = Annotated[list[JsonNum], Field(min_length=4, max_length=4), _fixed_numbers(4)]


def _span(value: Any) -> list[int | float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("expected [start, end]")
    start, end = _number(value[0]), _number(value[1])
    if start > end:
        raise ValueError("span start must be <= end")
    return [start, end]


# Scene and verdict schemas describe start <= end but do not enforce it.
# This alias does enforce it for models we author. Schema-backed spans use SpanLoose.
# The exported schema only carries the length; start <= end stays on the validator.
Span = Annotated[list[JsonNum], Field(min_length=2, max_length=2), BeforeValidator(_span)]


def _span_loose(value: Any) -> list[int | float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("expected [start, end]")
    return [_number(value[0]), _number(value[1])]


SpanLoose = Annotated[list[JsonNum], Field(min_length=2, max_length=2), BeforeValidator(_span_loose)]


def _pattern(expression: re.Pattern[str]):
    def check(value: Any) -> str:
        if not isinstance(value, str) or expression.fullmatch(value) is None:
            raise ValueError(f"does not match {expression.pattern}")
        return value

    return BeforeValidator(check)


IsoTime = Annotated[str, StringConstraints(pattern=_ISO_TIME.pattern), _pattern(_ISO_TIME)]
# pydantic-core's regex engine rejects the lookaheads, so the pattern is published with WithJsonSchema.
# The BeforeValidator still checks it with the stdlib, which is what the copied packet schema uses too.
RelPath = Annotated[str, _pattern(_REL_PATH), WithJsonSchema({"type": "string", "pattern": _REL_PATH.pattern})]
Sha256Hex = Annotated[str, StringConstraints(pattern=_SHA256.pattern), _pattern(_SHA256)]


class Strict(BaseModel):
    """Reject unknown fields. Wire names are the field names (one alias: `class`)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class Loose(BaseModel):
    """Allow extras where the JSON Schema leaves additionalProperties open."""

    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)


def keep_null(**kwargs: Any) -> Any:
    """Optional field that round-trips an explicit JSON null."""

    extra = {"keep_null": True}
    return Field(default=None, json_schema_extra=extra, **kwargs)


def dump_json(model: BaseModel) -> dict[str, Any]:
    """JSON object for round trips: omit unset optionals, keep explicit nulls."""

    return _dump_model(model)


def canonical_json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _dump_model(model: BaseModel) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, field in type(model).model_fields.items():
        if name not in model.model_fields_set:
            continue
        alias = field.alias or name
        value = getattr(model, name)
        extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        if value is None:
            if extra.get("keep_null"):
                out[alias] = None
            continue
        out[alias] = _dump_value(value)
    extras = model.__pydantic_extra__ or {}
    for key, value in extras.items():
        out[key] = _dump_value(value)
    return out


def _dump_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _dump_model(value)
    if isinstance(value, list):
        return [_dump_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _dump_value(item) for key, item in value.items()}
    return value
