"""Stub ``reads`` stage. Meter number is the synthetic placeholder, never a real plate."""

from __future__ import annotations

import json
from pathlib import Path

from server.contracts.jobs import StageContext, StageResult
from server.orchestrator.stubs._common import json_bytes, write_files

_STUB = Path(__file__).resolve().parents[1] / "tests" / "data" / "reads.stub.json"


def run(ctx: StageContext) -> StageResult:
    raw = json.loads(_STUB.read_text()) if _STUB.is_file() else {"meter_number": "000000000"}
    body = {"format": "base-reads/v0", "capture_id": ctx.capture_id, "facts": [], "meter_number": raw["meter_number"]}
    return write_files(ctx, {"reads/reads.json": json_bytes(body)})
