"""Stub ``criteria`` stage. Decisions stay ``manual_review`` so a stub cannot be an unsafe pass."""

from __future__ import annotations

from server.contracts.jobs import StageContext, StageResult
from server.eval.sceneio import schema_errors
from server.orchestrator.stubs._common import json_bytes, verdict_document, write_files


def run(ctx: StageContext) -> StageResult:
    files = {}
    for profile in ("A", "B", "C", "S"):
        verdict = verdict_document(profile, ctx.capture_id)
        errors = schema_errors(verdict)
        if errors:
            raise RuntimeError("artifact_invalid:criteria " + "; ".join(errors[:5]))
        files[f"verdicts/verdict.{profile}.json"] = json_bytes(verdict)
    files["verdicts/criteria_report.json"] = json_bytes({"status": "stub", "decidability": {"share": 0}})
    files["verdicts/placement.C.json"] = json_bytes({"profile": "C", "candidates": []})
    return write_files(ctx, files)
