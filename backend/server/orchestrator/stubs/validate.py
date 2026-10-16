"""Stub ``validate`` stage.

A scripted ``HSS_LOCAL_OUTCOME=failed`` (dev + stage stubs only) ends the run
here, on outcome row 1: the stage fails while its validation stand-in reports
unusable images. A failed stage never publishes artifacts, so the stand-in
registers its own artifact before raising.
"""

from __future__ import annotations

from server.contracts.jobs import StageContext, StageResult
from server.orchestrator.stubs._common import json_bytes, scripted_outcome, write_files


def run(ctx: StageContext) -> StageResult:
    if scripted_outcome() == "failed":
        _script_failed(ctx)
    return write_files(
        ctx,
        {
            "packet/validation.json": json_bytes({"imagesUsable": True, "ok": True, "issues": []}),
            "packet/normalized.json": json_bytes({"format": "house-capture-packet", "stub": True}),
            "packet/capture_check.json": json_bytes({"complete": False, "missing": ["meter_close", "meter_oblique"]}),
        },
    )


def _script_failed(ctx: StageContext) -> None:
    """Publish the validation stand-in, then fail the stage.

    Row 1 of ``server/orchestrator/outcomes.py`` is the one row whose capture
    status is ``failed``: the validate stage failed while
    ``packet/validation.json`` says the images are unusable. The stand-in
    registers itself on the run record directly, because a failing stage's
    ``StageResult`` is discarded.
    """
    key = "packet/validation.json"
    body = json_bytes(
        {
            "imagesUsable": False,
            "ok": False,
            "issues": ["scripted_local_test"],
            "reason": "HSS_LOCAL_OUTCOME=failed",
        }
    )
    ctx.storage.put_bytes(key, body, "application/json")
    run = ctx.state.get_run(ctx.run_id)
    run.artifacts[key] = key
    ctx.state.put_run(run)
    raise RuntimeError(
        "local_scripted_failure: HSS_LOCAL_OUTCOME=failed ends the run on outcome row 1 "
        "(validate failed, images unusable)"
    )
