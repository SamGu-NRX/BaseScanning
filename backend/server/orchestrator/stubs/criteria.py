"""Stub ``criteria`` stage. Decisions stay ``manual_review`` so a stub cannot be an unsafe pass.

A scripted local outcome (``HSS_LOCAL_OUTCOME``, dev + stage stubs only) is the
one exception: it writes the decision the outcome table's rows 11-14 need to
land the capture on the chosen state. Nothing else changes.
"""

from __future__ import annotations

from server.contracts.jobs import StageContext, StageResult
from server.eval.sceneio import schema_errors
from server.orchestrator.stubs._common import (
    json_bytes,
    scripted_outcome,
    scripted_verdict,
    verdict_document,
    write_files,
)


def run(ctx: StageContext) -> StageResult:
    outcome = scripted_outcome()
    if outcome == "not_eligible" and not getattr(ctx.settings, "SHOW_REJECT_TO_HOMEOWNER", False):
        raise RuntimeError(
            "HSS_LOCAL_OUTCOME=not_eligible needs SHOW_REJECT_TO_HOMEOWNER=1; "
            "with the reject hidden the capture would land manual_review instead"
        )
    files = {}
    for profile in ("A", "B", "C", "S"):
        verdict = (
            verdict_document(profile, ctx.capture_id)
            if outcome in ("manual_review", "failed")
            else scripted_verdict(profile, ctx.capture_id, outcome)
        )
        errors = schema_errors(verdict)
        if errors:
            raise RuntimeError("artifact_invalid:criteria " + "; ".join(errors[:5]))
        files[f"verdicts/verdict.{profile}.json"] = json_bytes(verdict)
    files["verdicts/criteria_report.json"] = json_bytes({"status": "stub", "decidability": {"share": 0}})
    files["verdicts/placement.C.json"] = json_bytes({"profile": "C", "candidates": []})
    return write_files(ctx, files)
