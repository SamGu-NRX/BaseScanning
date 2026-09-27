"""Validate device payloads against the copied schemas and, when present, WS-00 models."""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

try:
    from server.contracts.result import ResultResponse
except ImportError:  # kit zip has no server package
    ResultResponse = None  # type: ignore[misc, assignment]

KIT_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = KIT_ROOT / "schemas"
_SNAKE = re.compile(r"[a-z]+_[a-z]")


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text())


def _jsonschema_errors(document: dict, schema: dict) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    return [
        "/".join(map(str, error.path)) + ": " + error.message[:200]
        for error in sorted(validator.iter_errors(document), key=lambda item: list(item.path))
    ]


def snake_case_keys(document: object) -> list[str]:
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if _SNAKE.search(key):
                    found.append(key)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(document)
    return found


def validate_packet_schema_only(pk: dict) -> list[str]:
    return _jsonschema_errors(pk, _schema("capture-packet.v0.4.schema.json"))


def validate_events(body: dict) -> list[str]:
    schema = _schema("api.v1.schema.json")
    wrapped = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/EventsResponse"}
    return _jsonschema_errors(body, wrapped)


def validate_result(result: dict) -> list[str]:
    errors = _jsonschema_errors(result, _schema("result.v0.schema.json"))
    errors.extend(f"snake_case key {key}" for key in snake_case_keys(result))
    if ResultResponse is None:
        errors.append("server.contracts.result.ResultResponse is not importable")
        return errors
    try:
        ResultResponse.model_validate(result)
    except Exception as exc:  # pydantic.ValidationError
        errors.append(str(exc).split("\n")[0][:300])
    return errors
