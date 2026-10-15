"""Stub ``dense`` stage."""

from __future__ import annotations

from server.contracts.jobs import StageContext, StageResult
from server.orchestrator.stubs._common import json_bytes, ply, write_files


def run(ctx: StageContext) -> StageResult:
    return write_files(
        ctx,
        {
            "dense/dense.ply": ply(),
            "dense/depth/": b"stub",
            "dense/dense_report.json": json_bytes({"status": "stub"}),
        },
    )
