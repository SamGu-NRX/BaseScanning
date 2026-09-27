"""Write JSON Schemas for the models that have no hand-written source."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from server.contracts.api import (
    ApiModels,
    CaptureCheckData,
    ConfirmationData,
    FailedData,
    FilesCommittedData,
    HintData,
    RetakeRequestData,
    StageData,
    VerdictReadyData,
)
from server.contracts.jobs import (
    CaptureRecord,
    CommittedFile,
    EventRecord,
    FileRecord,
    RunRecord,
    StageRecord,
    StageResult,
)
from server.contracts.result import ResultResponse
from server.contracts.scale import ScaleBlock
from server.contracts.validation import ValidationReport

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
_DRAFT = "https://json-schema.org/draft/2020-12/schema"


def _document(title: str, schema: dict[str, Any]) -> dict[str, Any]:
    schema["$schema"] = _DRAFT
    schema["title"] = title
    return schema


def _merge_event_payloads(schema: dict[str, Any]) -> dict[str, Any]:
    """Publish the typed event bodies builders import, without making them request bodies."""

    payloads = TypeAdapter(
        FilesCommittedData
        | HintData
        | CaptureCheckData
        | StageData
        | VerdictReadyData
        | RetakeRequestData
        | FailedData
        | ConfirmationData
    )
    incoming = payloads.json_schema(mode="validation").get("$defs", {})
    schema.setdefault("$defs", {}).update(incoming)
    hint = schema["$defs"]["HintData"]
    hint.setdefault("allOf", []).append(
        {
            "if": {"properties": {"confirm": {"type": "object"}}, "required": ["confirm"]},
            "then": {"properties": {"code": {"const": "meter_number_confirm"}}},
        }
    )
    return schema


def _require_confirmation_value(schema: dict[str, Any]) -> dict[str, Any]:
    """`ConfirmationRequest.value_unless_none` is a model validator; mirror it in the exported schema."""

    request = schema["$defs"]["ConfirmationRequest"]
    request.setdefault("allOf", []).append(
        {
            "if": {"properties": {"kind": {"enum": ["confirmed", "corrected"]}}, "required": ["kind"]},
            "then": {"properties": {"value": {"type": "string"}}, "required": ["value"]},
        }
    )
    return schema


def _require_placement_when_eligible(schema: dict[str, Any]) -> dict[str, Any]:
    """`Outcome.eligible_has_placement` is a model validator; mirror it in the exported schema."""

    schema.setdefault("allOf", []).append(
        {
            "if": {
                "properties": {"outcome": {"properties": {"kind": {"const": "eligible"}}, "required": ["kind"]}},
                "required": ["outcome"],
            },
            "then": {"properties": {"outcome": {"required": ["recommendedPlacement"]}}},
        }
    )
    return schema


def schemas() -> dict[str, dict[str, Any]]:
    jobs = TypeAdapter(CaptureRecord | FileRecord | CommittedFile | RunRecord | StageRecord | EventRecord | StageResult)
    return {
        "result.v0.schema.json": _document(
            "Result v0",
            _require_placement_when_eligible(ResultResponse.model_json_schema(mode="validation")),
        ),
        "api.v1.schema.json": _document(
            "Ingest API v1",
            _require_confirmation_value(_merge_event_payloads(TypeAdapter(ApiModels).json_schema(mode="validation"))),
        ),
        "jobs.v0.schema.json": _document("Jobs v0", jobs.json_schema(mode="validation")),
        "scale.v0.schema.json": _document("Scale block v0", ScaleBlock.model_json_schema(mode="validation")),
        "validation-report.v0.schema.json": _document(
            "Validation report v0",
            ValidationReport.model_json_schema(mode="validation"),
        ),
    }


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m server.contracts.export_schemas")
    parser.add_argument("--check", action="store_true", help="exit 1 when committed schemas differ")
    args = parser.parse_args(argv)
    drifted: list[str] = []
    for name, schema in schemas().items():
        path = SCHEMA_DIR / name
        text = render(schema)
        if args.check:
            if not path.is_file() or path.read_text() != text:
                drifted.append(name)
        else:
            path.write_text(text)
    if drifted:
        print("schema drift: " + ", ".join(drifted), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
