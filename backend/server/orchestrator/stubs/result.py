"""Stub ``result`` stage. Emits the one ``verdict_ready`` and does not edit the verdict.

With ``HSS_LOCAL_OUTCOME`` (dev + stage stubs only) the stub scripts the
terminal state instead: the verdict the criteria stub writes lands the capture
on the chosen outcome-table row, and this stage composes the matching device
result. Every scripted message says it is a local test outcome, not a check of
a wall. The review page is honest for both modes: every stage is a stand-in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import numpy as np

from server.contracts.jobs import STAGE_DEPS, Profile, StageContext, StageResult
from server.contracts.result import (
    BoxArkitWorld,
    BoxSceneM,
    FootprintM,
    MeterBlock,
    Outcome,
    RecommendedPlacement,
    ResultResponse,
    ViewNeededDevice,
)
from server.orchestrator.stubs._common import json_bytes, scripted_outcome, sha256, write_files
from server.result import ar
from server.result.compose import FOOTPRINT
from server.result.copy import FALLBACK, Catalogue, load_catalogue, render, select_prompt

_PROFILES: frozenset[str] = frozenset({"A", "B", "C", "S"})

# Outcome kind to capture status, exactly the real composer's map.
_STATUS = {
    "eligible": "complete",
    "needs_more_photos": "needs_views",
    "not_eligible": "complete",
    "manual_review": "manual_review",
}

_MESSAGE = {
    "eligible": (
        "Local test stand-in: the proposed placement is scripted, not measured. "
        "This is a local test outcome, not a check of a wall."
    ),
    "needs_more_photos": (
        "Local test stand-in: the retake request is scripted, not measured. "
        "This is a local test outcome, not a check of a wall."
    ),
    "not_eligible": (
        "Local test stand-in: the not-eligible decision is scripted, not measured. "
        "This is a local test outcome, not a check of a wall."
    ),
}

# The profile-C footprint fallback is FOOTPRINT["C"] (real composer's table).
_STANDOFF_M = 0.02

# The committed prompt catalogue; the production catalogue may not exist here.
_PROMPTS = Path(__file__).resolve().parents[2] / "result" / "tests" / "data" / "prompts.en-US.json"


def run(ctx: StageContext) -> StageResult:
    outcome = scripted_outcome()
    if outcome == "failed":
        raise RuntimeError("HSS_LOCAL_OUTCOME=failed must end the run at validate; the result stage should not run")
    if outcome == "not_eligible" and not getattr(ctx.settings, "SHOW_REJECT_TO_HOMEOWNER", False):
        raise RuntimeError(
            "HSS_LOCAL_OUTCOME=not_eligible needs SHOW_REJECT_TO_HOMEOWNER=1; "
            "with the reject hidden the capture would land manual_review instead"
        )
    run_rec = ctx.state.get_run(ctx.run_id)
    key = run_rec.artifacts["verdicts/verdict.C.json"]
    verdict_bytes = ctx.storage.get_bytes(key)
    digest = sha256(verdict_bytes)
    verdict = json.loads(verdict_bytes)
    decision = verdict["site"]["decision"]
    kind = "manual_review" if outcome == "manual_review" else outcome
    ctx.state.append_event(
        ctx.capture_id,
        "verdict_ready",
        {"runId": ctx.run_id, "kind": kind},
        run_id=ctx.run_id,
    )
    if outcome == "manual_review":
        # The default run: today's bytes, unchanged.
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
    else:
        views = _views_needed() if outcome == "needs_more_photos" else []
        epoch = _packet_epoch(ctx)
        response = ResultResponse(
            runId=ctx.run_id,
            status=_STATUS[outcome],
            viewsNeeded=list(views),
            memberActions=[],
            outcome=Outcome(
                kind=kind,  # type: ignore[arg-type]
                profile=_profile(ctx),
                message=_MESSAGE[outcome],
                viewsNeeded=list(views),
                reasons=[],
                arkitEpoch=epoch,
                recommendedPlacement=_placement(_profile(ctx), epoch) if outcome == "eligible" else None,
            ),
            meter=_stub_meter(ctx),
        )
    body = response.model_dump(mode="json")
    ResultResponse.model_validate(body)
    internal = {"verdict_c_sha256": digest, "decision": decision}
    if outcome != "manual_review":
        internal["scripted_outcome"] = outcome
    return write_files(
        ctx,
        {
            "result/result.json": json_bytes(body),
            "result/result.internal.json": json_bytes(internal),
            "result/review.html": _review_page(outcome),
            "result/preview.ply": b"ply\nformat ascii 1.0\nelement vertex 0\nend_header\n",
        },
    )


def _placement(profile: str, epoch: str | None) -> RecommendedPlacement:
    """A proposed spot on the stub wall, in ARKit world when the packet has an epoch.

    The stub scene is meter-anchored with scale 1.0, so the scene-to-ARKit
    transform is the identity: the box pose is the meter-frame box pose, and
    the epoch comes from the capture packet's own ``epochs`` list. The
    footprint comes from the real composer's ``FOOTPRINT`` table, so the
    scripted proposal exercises the configured profile's product geometry.
    """
    width, height, depth = FOOTPRINT.get(profile, FOOTPRINT["C"])
    size = [width, height, depth]
    center = [0.6937, height / 2, depth / 2 + _STANDOFF_M]
    box_arkit = None
    if epoch is not None:
        built = ar.box_to_arkit(np.eye(4), center, size, 0.0)
        box_arkit = BoxArkitWorld(pose=built["pose"], size=built["size"])
    return RecommendedPlacement(
        wallId="w1",
        startSM=0.3,
        footprintM=FootprintM(w=width, h=height, d=depth),
        boxSceneM=BoxSceneM(centerM=center, sizeM=size, yawDeg=0.0),
        confidence=0.5,
        profile=profile,  # type: ignore[arg-type]
        boxArkitWorld=box_arkit,
    )


def _packet_epoch(ctx: StageContext) -> str | None:
    """The packet's ARKit epoch, when the capture carries exactly one.

    Same rule as ``server.result.ar.epoch_id``: the stub scene frame names no
    ``source_frame``, so a single packet epoch is the epoch; anything else is
    None and the placement stays in scene metres.
    """
    epochs = _packet_epochs(ctx)
    if len(set(epochs)) == 1:
        return epochs[0]
    return None


def _packet_epochs(ctx: StageContext) -> list[str]:
    """The packet's epoch ids, as the real composer feeds ``ar.epoch_id``.

    Packet ``epochs`` entries are objects like ``{"id": "e1"}``; plain strings
    are tolerated for older hand-written packets.
    """
    try:
        raw = (Path(str(ctx.inputs["packet_dir"])) / "packet.json").read_text()
        packet = json.loads(raw)
    except (KeyError, OSError, json.JSONDecodeError, TypeError):
        return []
    epochs = packet.get("epochs") if isinstance(packet, dict) else None
    if not isinstance(epochs, list):
        return []
    ids = []
    for item in epochs:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str):
            ids.append(item["id"])
    return ids


def _views_needed() -> list[ViewNeededDevice]:
    """One scripted band view, prompted from the real catalogue with a real id."""
    catalogue = _catalogue()
    entry = select_prompt(catalogue, kind="band", band="wall", cause="unobserved")
    prompt = render(entry, side="left", s_ft=2.0, out_ft=10.0)
    return [
        ViewNeededDevice(
            id="stub-wall-band",
            kind="band",
            criteria=["stub"],
            why="Local test stand-in: this retake request is scripted, not measured.",
            promptId=prompt.promptId,
            prompt={"title": prompt.title, "body": prompt.body},
            band="wall",
            wallId="w1",
            sM=[0.2, 1.0],
            side="left",
            viewingAngleDeg=[0, 30],
        )
    ]


def _catalogue() -> Catalogue:
    try:
        return load_catalogue(_PROMPTS)
    except (OSError, FileNotFoundError):
        return FALLBACK


def _review_page(outcome: str) -> bytes:
    stages = ", ".join(STAGE_DEPS)
    return (
        "<html><body><h1>Local stage-stub run</h1>"
        f"<p>Every stage here is a stand-in: stage stubs ran instead of real processing ({stages}).</p>"
        f"<p>Scripted outcome: <strong>{outcome}</strong> (HSS_LOCAL_OUTCOME). "
        "This is a local test, not a check of a wall.</p>"
        "</body></html>"
    ).encode()


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
