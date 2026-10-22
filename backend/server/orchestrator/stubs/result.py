"""Stub ``result`` stage. Emits the one ``verdict_ready`` and does not edit the verdict."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from server.contracts.jobs import Profile, StageContext, StageResult
from server.contracts.result import MeterBlock, Outcome, ResultResponse
from server.orchestrator.stubs._common import json_bytes, sha256, write_files

_PROFILES: frozenset[str] = frozenset({"A", "B", "C", "S"})


def run(ctx: StageContext) -> StageResult:
    run_rec = ctx.state.get_run(ctx.run_id)
    key = run_rec.artifacts["verdicts/verdict.C.json"]
    hydrated = Path(ctx.work_dir) / "verdicts/verdict.C.json"
    verdict_bytes = hydrated.read_bytes() if hydrated.is_file() else ctx.storage.get_bytes(key)
    digest = sha256(verdict_bytes)
    verdict = json.loads(verdict_bytes)
    decision = verdict["site"]["decision"]
    kind = "manual_review" if decision == "manual_review" else decision
    if not ctx.inputs.get("correction_snapshot"):
        ctx.state.append_event(
            ctx.capture_id,
            "verdict_ready",
            {"runId": ctx.run_id, "kind": kind},
            run_id=ctx.run_id,
        )
    else:
        out = Path(ctx.work_dir) / "result"
        out.mkdir(parents=True, exist_ok=True)
        (out / "publication.json").write_text(
            json.dumps(
                {
                    "captureId": ctx.capture_id,
                    "events": [["verdict_ready", {"runId": ctx.run_id, "kind": kind}]],
                    "status": "manual_review",
                    "deliverWebhook": False,
                }
            )
        )
    response = ResultResponse(
        runId=ctx.run_id,
        status="manual_review",
        viewsNeeded=[],
        memberActions=[],
        outcome=Outcome(
            kind="manual_review",
            profile=_profile(ctx),
            message="Review required before a placement is shown.",
            viewsNeeded=[],
            reasons=[],
        ),
        meter=_stub_meter(ctx),
    )
    body = response.model_dump(mode="json")
    ResultResponse.model_validate(body)
    internal = {"verdict_c_sha256": digest, "decision": decision}
    return write_files(
        ctx,
        {
            "result/result.json": json_bytes(body),
            "result/result.internal.json": json_bytes(internal),
            "result/review.html": b"<html><body>stub review</body></html>",
            "result/preview.ply": b"ply\nformat ascii 1.0\nelement vertex 0\nend_header\n",
        },
    )


def _profile(ctx: StageContext) -> Profile:
    configured = getattr(ctx.settings, "RULES_PROFILE_DEFAULT", "C")
    if configured in _PROFILES:
        return configured  # type: ignore[return-value]
    return "C"


def _meter_status(raw: str) -> Literal["auto_filled", "prefill_confirm", "unreadable", "not_captured"]:
    """The real composer's meter mapping, with autofill enabled."""
    if raw == "auto_fill":
        return "auto_filled"
    if raw in {"prefill", "choose", "single_reader"}:
        return "prefill_confirm"
    if raw == "unreadable":
        return "unreadable"
    return "not_captured"


def _stub_meter(ctx: StageContext) -> MeterBlock:
    """The reads-derived meter, as the real composer reports it.

    The result stage hydrates ``reads/reads.json``; serving the meter from it is
    what makes a recomposition after a late confirmation observable on
    ``GET /result`` without editing the verdict. The mapping mirrors
    ``server.result.meter.meter_block`` with autofill enabled.
    """

    try:
        doc = json.loads((Path(ctx.work_dir) / "reads" / "reads.json").read_text())
        meter = doc.get("meter") or {}
    except (OSError, json.JSONDecodeError, AttributeError):
        meter = {}
    status = _meter_status(str(meter.get("status") or ""))
    value = meter.get("value")
    shown = str(value) if isinstance(value, str) else None
    if status == "not_captured":
        shown = None
    candidates = [
        str(item.get("value"))
        for item in (meter.get("candidates") or [])
        if isinstance(item, dict) and item.get("value")
    ][:3]
    return MeterBlock(
        status=status,
        value=shown,
        candidates=candidates,
        barcodeConfirmed=bool(meter.get("barcode_match")),
        readersAgree=bool(meter.get("agree")),
        clean=False,
    )
