"""Stub ``result`` stage. Emits the one ``verdict_ready`` and does not edit the verdict."""

from __future__ import annotations

import json

from server.contracts.jobs import Profile, StageContext, StageResult
from server.contracts.result import Outcome, ResultResponse
from server.orchestrator.stubs._common import json_bytes, sha256, write_files

_PROFILES: frozenset[str] = frozenset({"A", "B", "C", "S"})


def run(ctx: StageContext) -> StageResult:
    run_rec = ctx.state.get_run(ctx.run_id)
    key = run_rec.artifacts["verdicts/verdict.C.json"]
    verdict_bytes = ctx.storage.get_bytes(key)
    digest = sha256(verdict_bytes)
    verdict = json.loads(verdict_bytes)
    decision = verdict["site"]["decision"]
    kind = "manual_review" if decision == "manual_review" else decision
    ctx.state.append_event(
        ctx.capture_id,
        "verdict_ready",
        {"runId": ctx.run_id, "kind": kind},
        run_id=ctx.run_id,
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
