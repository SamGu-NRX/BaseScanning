"""Stub ``validate`` stage."""

from __future__ import annotations

from server.contracts.jobs import StageContext, StageResult
from server.orchestrator.stubs._common import json_bytes, write_files


def run(ctx: StageContext) -> StageResult:
    return write_files(
        ctx,
        {
            "packet/validation.json": json_bytes({"imagesUsable": True, "ok": True, "issues": []}),
            "packet/normalized.json": json_bytes({"format": "house-capture-packet", "stub": True}),
            "packet/capture_check.json": json_bytes({"complete": False, "missing": ["meter_close", "meter_oblique"]}),
        },
    )
