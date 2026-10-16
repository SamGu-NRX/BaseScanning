"""Stub ``scale`` stage."""

from __future__ import annotations

from server.contracts.jobs import StageContext, StageResult
from server.orchestrator.stubs._common import json_bytes, write_files


def run(ctx: StageContext) -> StageResult:
    body = {"status": "verified", "scale_factor": 1.0, "origin_m": [0, 0, 0]}
    return write_files(ctx, {"scale/scale.json": json_bytes(body)})
