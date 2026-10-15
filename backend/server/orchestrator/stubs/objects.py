"""Stub ``objects`` stage (2D)."""

from __future__ import annotations

from server.contracts.jobs import StageContext, StageResult
from server.orchestrator.stubs._common import json_bytes, write_files


def run(ctx: StageContext) -> StageResult:
    body = {"format": "hss-objects/v0", "objects": [], "ground_type": []}
    return write_files(ctx, {"objects/objects.json": json_bytes(body)})
