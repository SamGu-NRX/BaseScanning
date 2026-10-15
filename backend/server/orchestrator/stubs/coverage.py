"""Stub ``coverage`` stage."""

from __future__ import annotations

from server.contracts.jobs import StageContext, StageResult
from server.orchestrator.stubs._common import json_bytes, ply, write_files


def run(ctx: StageContext) -> StageResult:
    return write_files(
        ctx,
        {
            "coverage/coverage.npz": b"stub",
            "coverage/bands.json": json_bytes({"bands": []}),
            "coverage/points.ply": ply(),
            "coverage/scene_draft.json": json_bytes({"format": "stub"}),
        },
    )
