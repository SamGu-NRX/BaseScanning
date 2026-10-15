"""Stub ``objects_3d`` stage."""

from __future__ import annotations

from server.contracts.jobs import StageContext, StageResult
from server.orchestrator.stubs._common import json_bytes, write_files


def run(ctx: StageContext) -> StageResult:
    body = {"format": "hss-objects-3d/v0", "objects": []}
    return write_files(ctx, {"objects/objects_3d.json": json_bytes(body)})
