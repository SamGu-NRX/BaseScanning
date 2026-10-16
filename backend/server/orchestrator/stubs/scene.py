"""Stub ``scene`` stage. The document validates as scene v0 when the harness is importable."""

from __future__ import annotations

import json

from server.contracts.jobs import StageContext, StageResult
from server.eval.sceneio import validate
from server.orchestrator.stubs._common import json_bytes, scene_document, write_files


def run(ctx: StageContext) -> StageResult:
    scene = scene_document(ctx.capture_id)
    validate(scene)
    return write_files(
        ctx,
        {
            "scene/scene.json": json_bytes(scene),
            "scene/build_report.json": json.dumps({"status": "stub"}).encode(),
        },
    )
