"""Compose the device result, the internal record, the preview, and the review page."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, get_args

import numpy as np
from pydantic import ValidationError

from server.contracts.jobs import CaptureRecord, EventRecord, RunRecord, StageContext, StageName, StageResult
from server.contracts.result import (
    BoxArkitWorld,
    BoxSceneM,
    CriterionSummary,
    FootprintM,
    MeterBlock,
    Outcome,
    RecommendedPlacement,
    ReferenceCrop,
    RejectEvidence,
    RejectReason,
    ResultResponse,
    ScaleSummary,
    SiteSummary,
    TargetRegionArkit,
    ViewNeededDevice,
)
from server.result import ar, notify
from server.result.config import ResultConfig, load_config
from server.result.copy import (
    FALLBACK,
    Catalogue,
    load_catalogue,
    render,
    select_prompt,
    side_text,
    what_text,
    worst_cause,
)
from server.result.harness import chain as chain_mod
from server.result.harness import frame as frame_mod
from server.result.harness import sceneio
from server.result.meter import meter_block
from server.result.preview import build_preview
from server.result.review.render import render_review

FOOTPRINT = {
    "A": (0.6096, 1.0033, 0.6096),
    "B": (0.6096, 1.0033, 0.6096),
    "C": (0.7874, 1.0033, 0.5588),
    "S": (0.7874, 1.0033, 0.5588),
}
_COVERAGE_WORST = ("unobserved", "generated", "partial", "grazing", "observed")
_IMPL_SCHEMA = "hss-stage-impl/v1"
Implementation = Literal["real", "stand-in"]
_DEFAULT_CONFIG = ResultConfig()
_TARGET_CLASSES = (
    "garage_door",
    "gas_meter",
    "ac_unit",
    "basement_well",
    "window",
    "door",
    "panel",
    "pool",
    "meter",
    "ground",
)


class ResultError(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(detail or code)


@dataclass
class ComposeResult:
    response: ResultResponse
    internal: dict
    events: list[tuple[str, dict]]
    capture_id: str
    run_id: str
    warnings: list[str] = field(default_factory=list)


def compose_result(
    capture: CaptureRecord,
    run: RunRecord,
    scene_dir: Path,
    verdict_dir: Path,
    out_dir: Path,
    *,
    reads: Path | None,
    scale: Path | None,
    catalogue: Catalogue,
    config: ResultConfig = _DEFAULT_CONFIG,
    epochs: list[str] | None = None,
    storage: object = None,
    settings: object = None,
    impl_dir: Path | None = None,
) -> ComposeResult:
    warnings: list[str] = []
    if catalogue.source == "fallback":
        warnings.append("catalogue_missing")
    scene = _load_scene(scene_dir)
    verdicts = _load_verdicts(verdict_dir)
    report = _read_json(verdict_dir / "criteria_report.json")
    scale_doc = _read_json(scale) if scale else None
    reads_doc = _read_json(reads) if reads else None
    if reads is None or reads_doc is None:
        warnings.append("reads_missing")
        reads_doc = None

    profile = config.profile if config.profile in {"A", "B", "C", "S"} else "C"
    verdict = verdicts.get(profile)
    if verdict is None and not run.failure_code:
        raise ResultError("verdict_missing", f"no verdict.{profile}.json")

    t_sa = ar.scene_to_arkit(scene["frame"])
    if t_sa is None:
        warnings.append("no_arkit_frame")
    epoch = ar.epoch_id(scene["frame"], epochs)

    if verdict is None:
        kind = "manual_review"
        true_decision = "manual_review"
        site_reasons = [str(run.failure_code)]
        criteria: list[CriterionSummary] = []
        views: list[ViewNeededDevice] = []
        placement = None
        route = None
    else:
        kind, true_decision, site_reasons = _lattice(verdict, run, config)
        criteria = _criteria(verdict, _chosen_id(verdict))
        placement = None
        route = None
        views = []
        if kind == "eligible":
            placement, route, route_warning = _placement(verdict, scene, profile, t_sa, verdict_dir)
            if route_warning:
                warnings.append(route_warning)
        if kind == "needs_more_photos":
            views = _views(verdict, scene, catalogue, t_sa, config.max_views)

    hidden_reject = true_decision == "reject" and not config.show_reject
    if hidden_reject:
        kind = "manual_review"
        device_decision = "manual_review"
        outcome_reasons = []
        site_reasons = []
        criteria = _mask_failed_criteria(criteria)
    else:
        device_decision = _device_decision(kind, true_decision)
        outcome_reasons = _reasons(verdict, catalogue) if kind == "not_eligible" and verdict else []

    if kind != "eligible":
        placement = None
        route = None
    if kind != "needs_more_photos":
        views = []

    actions = _member_actions(scale_doc)
    headline = render(select_prompt(catalogue, outcome=kind), **_vars(None, None, None, None)).title
    status = {
        "eligible": "complete",
        "needs_more_photos": "needs_views",
        "not_eligible": "complete",
        "manual_review": "manual_review",
    }[kind]
    meter = meter_block(reads_doc, autofill=config.meter_autofill)
    scale_summary = _scale_summary(scale_doc)

    outcome = Outcome(
        kind=kind,
        profile=profile,  # type: ignore[arg-type]
        message=headline,
        viewsNeeded=views,
        reasons=outcome_reasons,
        arkitEpoch=epoch,
        frameToArkit=frame_mod.mat4_to_list(t_sa) if t_sa is not None else None,
        recommendedPlacement=placement,
        cableRoutePolylineArkit=route,
    )
    try:
        response = ResultResponse(
            runId=run.run_id,
            status=status,  # type: ignore[arg-type]
            site=SiteSummary(decision=device_decision, reasons=site_reasons, profile=profile),  # type: ignore[arg-type]
            scale=scale_summary,
            criteria=criteria,
            viewsNeeded=views,
            memberActions=actions,  # type: ignore[arg-type]
            meter=meter,
            outcome=outcome,
            verdictUrl=None if hidden_reject else (f"verdict.{profile}.json" if verdict else None),
            previewUrl="preview.ply",
            reviewUrl="review.html",
        )
    except ValidationError as exc:
        raise ResultError("payload_invalid", str(exc)) from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    preview_warning = build_preview(scene, scene_dir, out_dir / "preview.ply", max_points=config.preview_max_points)
    if preview_warning:
        warnings.append(preview_warning)
        response = response.model_copy(update={"previewUrl": None})

    implementations, stand_ins = stage_implementations(impl_dir)
    internal = {
        "decision": true_decision,
        "outcome_kind_internal": "not_eligible" if true_decision == "reject" else kind,
        "failure_code": run.failure_code,
        "degraded": list(run.degraded),
        "judge_flags": _judge_flags(report, verdict_dir),
        "c2_vetoes": (report or {}).get("c2", {}).get("vetoes") if report else None,
        "decidability": (report or {}).get("decidability") if report else None,
        "warnings": warnings,
        "stageImplementations": implementations,
        "standIns": stand_ins,
    }
    # A hidden reject keeps the true kind even though `kind` was rewritten.
    if true_decision == "reject" and not config.show_reject:
        internal["outcome_kind_internal"] = "not_eligible"

    payload = response.model_dump(mode="json")
    payload.pop("verdict", None)
    (out_dir / "result.json").write_text(_dumps(payload))
    (out_dir / "result.internal.json").write_text(_dumps(internal))
    render_review(
        _review_context(
            capture,
            run,
            verdicts,
            profile,
            report,
            scene,
            response,
            internal,
            meter,
            reads_doc,
            storage=storage,
            settings=settings,
        ),
        out_dir / "review.html",
        three_url=config.threejs_url,
    )

    # Exactly one final event. A retake replaces verdict_ready; the two are not both sent.
    if kind == "needs_more_photos" or actions:
        events = [("retake_request", notify.retake_request(run.run_id, [v.id for v in views], list(actions)))]
    else:
        events = [("verdict_ready", notify.verdict_ready(run.run_id, kind))]
    return ComposeResult(response, internal, events, capture.capture_id, run.run_id, warnings)


def emit_events(result: ComposeResult, state) -> list[int]:
    """Publish the run's final event once per composition.

    A retry of the result stage finds its final event and returns those seqs. A
    late correction re-runs the stage under the same run id, and its confirmation
    event lands after the old final: finals older than the latest confirmation
    are stale, so the recomposed result publishes its own.
    """
    prior = _all_events(state, result.capture_id)
    finals = {"verdict_ready", "retake_request"}
    corrected = max((ev.seq for ev in prior if ev.type == "confirmation"), default=0)
    already = [ev for ev in prior if ev.run_id == result.run_id and ev.type in finals and ev.seq > corrected]
    if already:
        return [ev.seq for ev in already]
    seqs = []
    for event_type, data in result.events:
        rec = state.append_event(result.capture_id, event_type, data, run_id=result.run_id, stage="result")
        seqs.append(rec.seq)
    return seqs


def _all_events(state, capture_id: str) -> list[EventRecord]:
    """Every event on the capture; events_after returns one page at a time."""
    events: list[EventRecord] = []
    while True:
        page = state.events_after(capture_id, events[-1].seq if events else 0)
        if not page:
            return events
        events.extend(page)


def _stage_input(ctx: StageContext, short: str, key: str) -> Path | None:
    """The short input key wins; orchestrated runs hydrate `work_dir/<key>` instead."""

    raw = ctx.inputs.get(short)
    if raw:
        return Path(raw)
    path = Path(ctx.work_dir) / key
    return path if path.is_file() else None


def run(ctx: StageContext) -> StageResult:
    config = load_config(ctx.settings)
    scene_dir = Path(ctx.inputs.get("scene") or Path(ctx.work_dir) / "scene")
    verdict_dir = Path(ctx.inputs.get("verdicts") or Path(ctx.work_dir) / "verdicts")
    reads = _stage_input(ctx, "reads", "reads/reads.json")
    scale = _stage_input(ctx, "scale", "scale/scale.json")
    out_dir = Path(ctx.inputs.get("out") or Path(ctx.work_dir) / "result")
    capture = ctx.state.get_capture(ctx.capture_id)
    run_rec = ctx.state.get_run(ctx.run_id)
    if capture is None or run_rec is None:
        raise ResultError("verdict_missing", "capture or run is not in the state store")
    catalogue, _missing = _catalogue(config.catalogue_path)
    epochs = _epochs(ctx)
    try:
        composed = compose_result(
            capture,
            run_rec,
            scene_dir,
            verdict_dir,
            out_dir,
            reads=reads,
            scale=scale,
            catalogue=catalogue,
            config=config,
            epochs=epochs,
            storage=ctx.storage,
            settings=ctx.settings,
            impl_dir=stage_impl_dir(ctx),
        )
    except ResultError:
        raise
    seqs = emit_events(composed, ctx.state)
    ctx.state.set_status(ctx.capture_id, composed.response.status)
    _deliver(composed, capture, config)
    artifacts = {
        "result/result.json": str(out_dir / "result.json"),
        "result/result.internal.json": str(out_dir / "result.internal.json"),
        "result/review.html": str(out_dir / "review.html"),
    }
    if (out_dir / "preview.ply").is_file():
        artifacts["result/preview.ply"] = str(out_dir / "preview.ply")
    return StageResult(artifacts=artifacts, metrics={"events": len(seqs)}, warnings=list(composed.warnings))


def stage_impl_dir(ctx: StageContext) -> Path | None:
    """The directory of hydrated ``impl/`` markers for this result stage, or None if there is none."""

    raw = ctx.inputs.get("impl")
    impl_dir = Path(raw) if raw else Path(ctx.work_dir) / "impl"
    return impl_dir if impl_dir.is_dir() else None


def stage_implementations(
    impl_dir: Path | None, *, result_implementation: Implementation = "real"
) -> tuple[dict[str, str], list[str]]:
    """Stage -> real | stand-in | unknown, from the impl files the runner publishes.

    The orchestration leaves one ``impl/<stage>.json`` per run stage in the result
    stage's inputs; ``stand-in`` means the stage ran a module under
    server.orchestrator.stubs, so its output is not real measurement work. A stage
    with no file is ``unknown`` — as for runs composed before this record existed.

    The result stage's own entry is ``result_implementation``, supplied by the
    callable that is composing: its marker is published only after it returns, and
    a recomposition finds the previous pass's marker instead of its own.

    A marker stops the composition with a loud error naming the file unless it
    carries the pinned schema, a known stage that matches its filename, and an
    implementation of real or stand-in. Without the filename check, ``objects.json``
    naming ``reads`` as a stand-in could be overwritten by ``reads.json`` naming it
    real, and the stand-in would vanish from the record.
    """

    known = get_args(StageName)
    implementations = {stage: "unknown" for stage in known}
    if impl_dir is not None and impl_dir.is_dir():
        for path in sorted(impl_dir.glob("*.json")):
            try:
                doc = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                raise ResultError("impl_invalid", f"{path}: unreadable impl file ({exc})") from exc
            if not isinstance(doc, dict) or doc.get("schema") != _IMPL_SCHEMA:
                raise ResultError("impl_invalid", f"{path}: schema is not {_IMPL_SCHEMA}")
            stage = doc.get("stage")
            if not isinstance(stage, str) or stage not in known:
                raise ResultError("impl_invalid", f"{path}: stage {stage!r} is not a pipeline stage")
            if stage != path.stem:
                raise ResultError("impl_invalid", f"{path}: names stage {stage!r} but the file is {path.stem!r}")
            implementation = doc.get("implementation")
            if not isinstance(implementation, str) or implementation not in get_args(Implementation):
                raise ResultError("impl_invalid", f"{path}: implementation {implementation!r} is not real or stand-in")
            implementations[stage] = implementation
    implementations["result"] = result_implementation
    stand_ins = sorted(stage for stage, impl in implementations.items() if impl == "stand-in")
    return implementations, stand_ins


def stand_in_notice(stand_ins: list[str]) -> str | None:
    """The reviewer-page sentence naming the stand-in stages, or None when there are none."""

    if not stand_ins:
        return None
    return f"Stand-in steps: {', '.join(stand_ins)}. Their output is not from this scan."


def _catalogue(path: Path) -> tuple[Catalogue, bool]:
    try:
        return load_catalogue(path), False
    except (FileNotFoundError, OSError):
        return FALLBACK, True


def _epochs(ctx: StageContext) -> list[str] | None:
    raw = ctx.inputs.get("epochs")
    if not raw:
        return None
    return [part for part in raw.split(",") if part]


def _deliver(composed: ComposeResult, capture: CaptureRecord, config: ResultConfig) -> None:
    secret = os.environ.get("WEBHOOK_HMAC_SECRET")
    if not config.webhook_url or not secret:
        return
    body = notify.webhook_body(
        event_id="evt_" + composed.run_id,
        capture_id=capture.capture_id,
        order_ref=capture.order_ref,
        run_id=composed.run_id,
        decision=str(composed.internal["decision"]),
        verdict_url=composed.response.verdictUrl,
    )
    try:
        notify.post_webhook(
            body,
            url=config.webhook_url,
            secret=secret,
            timestamp=int(time.time()),
        )
    except Exception:
        composed.warnings.append("webhook_failed")


def _load_scene(scene_dir: Path) -> dict:
    path = scene_dir / "scene.json"
    try:
        scene = json.loads(path.read_text())
        sceneio.validate(scene)
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        raise ResultError("scene_invalid", str(exc)) from exc
    return scene


def _load_verdicts(verdict_dir: Path) -> dict[str, dict]:
    found: dict[str, dict] = {}
    if not verdict_dir.is_dir():
        return found
    for path in sorted(verdict_dir.glob("verdict.*.json")):
        profile = path.stem.split(".", 1)[1]
        try:
            doc = json.loads(path.read_text())
            sceneio.validate(doc)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ResultError("verdict_invalid", f"{path.name}: {exc}") from exc
        found[profile] = doc
    return found


def _read_json(path: Path | None) -> dict | None:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text())


def _lattice(verdict: dict, run: RunRecord, config: ResultConfig) -> tuple[str, str, list[str]]:
    decision = verdict["site"]["decision"]
    views = verdict.get("views_needed") or []
    reasons = list(verdict["site"].get("reasons") or [])
    if decision == "pass":
        kind = "eligible"
    elif decision == "manual_review" and views:
        kind = "needs_more_photos"
    elif decision == "manual_review":
        kind = "manual_review"
    else:
        kind = "not_eligible"
    if (run.degraded or run.failure_code) and kind == "eligible":
        kind = "manual_review"
        reasons += [f"degraded:{stage}" for stage in run.degraded]
        if run.failure_code and run.failure_code not in reasons:
            reasons.append(run.failure_code)
    elif run.degraded and kind != "eligible":
        reasons += [f"degraded:{stage}" for stage in run.degraded]
    _ = config
    return kind, decision, reasons


def _device_decision(kind: str, true_decision: str) -> str:
    if kind == "eligible":
        return "pass"
    if kind == "not_eligible":
        return "reject"
    if true_decision == "reject":
        return "manual_review"
    return "manual_review"


def _chosen_id(verdict: dict) -> str | None:
    chosen = (verdict.get("site") or {}).get("chosen_candidate")
    if chosen:
        return str(chosen)
    candidates = verdict.get("candidates") or []
    passes = [c for c in candidates if c.get("outcome") == "pass"]
    pool = passes or candidates
    if not pool:
        return None

    def distance(cand: dict) -> float:
        span = cand.get("start_s_m") or [0, 0]
        return min(abs(float(span[0])), abs(float(span[1])))

    return str(min(pool, key=distance)["id"])


def _candidate(verdict: dict) -> dict | None:
    cid = _chosen_id(verdict)
    for cand in verdict.get("candidates") or []:
        if cand.get("id") == cid:
            return cand
    return None


def _spot_candidate_ids(verdict: dict, chosen_id: str | None) -> set[str]:
    """Ids that mean the chosen spot.

    WS-09 (PR 39) builds criteria before it assigns ``runN`` ids, so the chosen
    spot's rows are stamped ``candidate_id: "best"`` while ``site.chosen_candidate``
    is ``runN``. When a row already uses the chosen id, that id is the spot.
    """
    used = {
        item.get("candidate_id")
        for item in verdict.get("criteria") or []
        if item.get("scope") == "candidate" and item.get("candidate_id")
    }
    if chosen_id and chosen_id in used:
        return {chosen_id}
    if "best" in used:
        return {"best"}
    if chosen_id:
        return {chosen_id}
    return set()


def _at_spot(item: dict, spot_ids: set[str]) -> bool:
    if item.get("scope") != "candidate":
        return True
    return item.get("candidate_id") in spot_ids


def _criteria(verdict: dict, chosen_id: str | None) -> list[CriterionSummary]:
    spot_ids = _spot_candidate_ids(verdict, chosen_id)
    rows = []
    for item in verdict.get("criteria") or []:
        if not _at_spot(item, spot_ids):
            continue
        measured = item.get("measured") or {}
        threshold = item.get("threshold") or {}
        rows.append(
            CriterionSummary(
                id=item["id"],
                outcome=item["outcome"],
                unsureCause=item.get("unsure_cause"),
                measuredFt=measured.get("value_ft"),
                plusMinusFt=measured.get("plus_minus_ft"),
                thresholdFt=threshold.get("value_ft"),
                coverage=_worst_coverage(item),
            )
        )
    return rows


def _worst_coverage(item: dict) -> str | None:
    found: list[str] = []
    for key in ("measured", "fact"):
        block = item.get(key) or {}
        for cov in block.get("coverage") or []:
            if cov.get("status"):
                found.append(cov["status"])
    if not found:
        return None
    return min(found, key=lambda status: _COVERAGE_WORST.index(status) if status in _COVERAGE_WORST else 99)


def _reasons(verdict: dict | None, catalogue: Catalogue) -> list[RejectReason]:
    if verdict is None:
        return []
    spot_ids = _spot_candidate_ids(verdict, _chosen_id(verdict))
    fails = [item for item in verdict.get("criteria") or [] if item.get("outcome") == "fail"]
    matched = [item for item in fails if _at_spot(item, spot_ids)]
    if not matched:
        # Older verdicts stamp every candidate-scope row ``candidate_id: "spot"``
        # while ``chosen_candidate`` is null. A shown reject still names those rows.
        matched = [item for item in fails if item.get("scope") == "candidate"]
    rows = []
    for item in matched:
        measured = item.get("measured") or {}
        threshold = item.get("threshold") or {}
        comparison = threshold.get("comparison")
        if comparison not in {"at_least", "at_most"}:
            comparison = None
        images = list((item.get("evidence") or {}).get("views") or [])
        boxes = [[0, 0, 0, 0] for _ in images]
        text = render(
            select_prompt(catalogue, criterion=item["id"]),
            **_vars(None, None, None, threshold.get("value_ft")),
        ).body
        rows.append(
            RejectReason(
                criterion=item["id"],
                measuredFt=measured.get("value_ft"),
                plusMinusFt=measured.get("plus_minus_ft"),
                thresholdFt=threshold.get("value_ft"),
                comparison=comparison,
                text=text,
                evidence=RejectEvidence(images=images, boxesPx=boxes),
            )
        )
    return rows


def _placement(verdict, scene, profile: str, t_sa, verdict_dir: Path):
    doc = _read_json(verdict_dir / f"placement.{profile}.json") or {}
    width, height, depth = FOOTPRINT.get(profile, FOOTPRINT["C"])
    chain = chain_mod.Chain(scene)
    cand = _candidate(verdict)
    box = doc.get("box_scene_m")
    if box:
        center = [float(v) for v in box["center_m"]]
        size = [float(v) for v in box["size_m"]]
        yaw = float(box["yaw_deg"])
        start = float(
            doc.get("start_s_m")
            if isinstance(doc.get("start_s_m"), int | float)
            else (cand or {}).get("start_s_m", [0, 0])[0]
        )
        wall_id = str(doc.get("wall_id") or (cand or {}).get("wall_id") or chain.walls[0].id)
    else:
        if cand is None:
            raise ResultError("payload_invalid", "eligible verdict has no candidate")
        span = [float(v) for v in cand["start_s_m"]]
        start, s_mid = _near_edge_placement(span, width)
        wall = ar._wall(chain, cand.get("wall_id"), s_mid)
        plan = np_plan(wall, s_mid)
        normal = wall.outward
        gy = float(chain_mod.ground_y(scene, plan.reshape(1, 2))[0])
        center = [
            float(plan[0] + normal[0] * (depth / 2 + ar.STANDOFF_M)),
            gy + height / 2,
            float(plan[1] + normal[1] * (depth / 2 + ar.STANDOFF_M)),
        ]
        size = [width, height, depth]
        yaw = ar.yaw_deg(wall.along)
        wall_id = wall.id
        start = float(start)
    confidence = _confidence(doc.get("checks_at_spot"), verdict, _chosen_id(verdict))
    box_arkit = None
    route = None
    warning = None
    if t_sa is not None:
        built = ar.box_to_arkit(t_sa, center, size, yaw)
        box_arkit = BoxArkitWorld(pose=built["pose"], size=built["size"])
        route_pts = doc.get("route_polyline_scene_m")
        if not route_pts:
            route_pts = _route(chain, scene, start, center, height)
            if route_pts is None:
                warning = "no_route_geometry"
        if route_pts:
            route = ar.points_to_arkit(t_sa, route_pts)
    else:
        warning = "no_route_geometry" if not doc.get("route_polyline_scene_m") else None
    placement = RecommendedPlacement(
        wallId=wall_id,
        startSM=float(start),
        footprintM=FootprintM(w=size[0], h=size[1], d=size[2]),
        boxSceneM=BoxSceneM(centerM=center, sizeM=size, yawDeg=yaw),
        boxArkitWorld=box_arkit,
        confidence=confidence,
        profile=profile,  # type: ignore[arg-type]
    )
    return placement, route, warning


def np_plan(wall, s: float):
    return np.asarray(wall.plan_at(s), float)


def _confidence(checks, verdict: dict, chosen_id: str | None) -> float:
    mous: list[float] = []
    for check in checks or []:
        if check.get("margin_over_uncertainty") is not None:
            mous.append(float(check["margin_over_uncertainty"]))
    if not mous:
        spot_ids = _spot_candidate_ids(verdict, chosen_id)
        for item in verdict.get("criteria") or []:
            if not _at_spot(item, spot_ids):
                continue
            if item.get("margin_over_uncertainty") is not None:
                mous.append(float(item["margin_over_uncertainty"]))
    if not mous:
        return 1.0
    return max(0.0, min(1.0, min((m - 1.0) / 2.0 for m in mous)))


def _near_edge(span: list[float]) -> float:
    """Endpoint closer to the meter at s = 0. A tie uses the smaller s."""
    a, b = float(span[0]), float(span[1])
    if abs(a) < abs(b):
        return a
    if abs(b) < abs(a):
        return b
    return min(a, b)


def _near_edge_placement(span: list[float], width: float) -> tuple[float, float]:
    """startSM is the near edge; the centre is always start + width/2 (WS-09)."""
    start = _near_edge(span)
    return start, start + width / 2


def _push_point(pts: list[list[float]], point: list[float]) -> None:
    if pts and max(abs(a - b) for a, b in zip(pts[-1], point, strict=True)) <= 1e-6:
        return
    pts.append(point)


def _route(chain, scene, start_s: float, center: list[float], height: float) -> list[list[float]] | None:
    if not chain.walls:
        return None
    pts = [[0.0, ar.ROUTE_HEIGHT_M, ar.STANDOFF_M]]
    lo, hi = sorted((0.0, float(start_s)))
    for corner in scene.get("chain", {}).get("corners") or []:
        s = float(corner.get("s_m", 0.0))
        if lo - 1e-6 <= s <= hi + 1e-6 and corner.get("point_m"):
            wall = chain.wall_at_s(s) or chain.nearest_wall(corner["point_m"])
            x, z = corner["point_m"]
            n = wall.outward
            _push_point(pts, [float(x + n[0] * ar.STANDOFF_M), ar.ROUTE_HEIGHT_M, float(z + n[1] * ar.STANDOFF_M)])
    near = float(start_s)
    wall = ar._wall(chain, None, near)
    plan = np_plan(wall, near)
    n = wall.outward
    _push_point(pts, [float(plan[0] + n[0] * ar.STANDOFF_M), ar.ROUTE_HEIGHT_M, float(plan[1] + n[1] * ar.STANDOFF_M)])
    _push_point(pts, [center[0], center[1] + height / 2, center[2]])
    return pts


def _mask_failed_criteria(rows: list[CriterionSummary]) -> list[CriterionSummary]:
    """A hidden reject keeps the row but not the criterion id or the measurements."""
    masked = []
    for row in rows:
        if row.outcome != "fail":
            masked.append(row)
            continue
        masked.append(
            row.model_copy(
                update={
                    "id": "review",
                    "outcome": "unsure",
                    "unsureCause": None,
                    "measuredFt": None,
                    "plusMinusFt": None,
                    "thresholdFt": None,
                }
            )
        )
    return masked


def _views(verdict: dict, scene: dict, catalogue: Catalogue, t_sa, max_views: int) -> list[ViewNeededDevice]:
    by_id = {item["id"]: item for item in verdict.get("criteria") or []}
    rows = []
    for view in (verdict.get("views_needed") or [])[:max_views]:
        causes = [((by_id.get(cid) or {}).get("unsure_cause")) for cid in view.get("criteria") or []]
        cause = worst_cause(causes)
        target_class = _target_class(view)
        entry = select_prompt(
            catalogue, kind=view.get("kind"), band=view.get("band"), cause=cause, target_class=target_class
        )
        span = view.get("s_m")
        s_mid = None if not span else (float(span[0]) + float(span[1])) / 2
        prompt = render(entry, **_vars(s_mid, view.get("out_m"), target_class, None))
        angle = _angle(view)
        surface, polygon = ar.region_polygon_scene(view, scene)
        region = None
        pose = None
        crop = None
        if t_sa is not None:
            region = TargetRegionArkit(surface=surface, polygon=ar.points_to_arkit(t_sa, polygon))  # type: ignore[arg-type]
            pose = frame_mod.mat4_to_list(frame_mod.transform_pose(t_sa, ar.camera_pose_for_view(view, scene)))
        else:
            found = ar.best_reference_crop(polygon, scene)
            if found:
                crop = ReferenceCrop(image=found["image"], boxPx=found["boxPx"])
        s_m = None
        if span and len(span) == 2:
            a, b = float(span[0]), float(span[1])
            s_m = [a, b] if a <= b else [b, a]
        rows.append(
            ViewNeededDevice(
                id=view["id"],
                kind=view["kind"],
                band=view.get("band"),
                wallId=view.get("wall_id"),
                sM=s_m,
                outM=view.get("out_m"),
                side=view.get("side"),
                target=view.get("target"),
                criteria=list(view.get("criteria") or []),
                why=view.get("why") or "We need another photo of this spot.",
                viewingAngleDeg=angle,
                promptId=prompt.promptId,
                prompt={"title": prompt.title, "body": prompt.body},
                targetRegionArkit=region,
                suggestedCameraPoseArkit=pose,
                referenceCrop=crop,
            )
        )
    return rows


def _angle(view: dict) -> list[float] | None:
    raw = view.get("view_angle_deg")
    if raw and len(raw) == 2:
        a, b = float(raw[0]), float(raw[1])
        return [a, b] if a <= b else [b, a]
    if view.get("kind") == "past_end" or view.get("band") == "wall":
        return [0, 30]
    if view.get("band") == "ground":
        return [20, 60]
    return None


def _target_class(view: dict) -> str | None:
    if view.get("target_class"):
        return str(view["target_class"])
    text = (view.get("target") or "").lower().replace(" ", "_")
    for name in _TARGET_CLASSES:
        if name in text:
            return name
    return None


def _vars(s_mid, out_m, target_class, threshold_ft) -> dict[str, str | float]:
    # Metres. `render` turns numeric s_ft / out_ft into whole feet, matching the kit.
    s_ft = 0.0 if s_mid is None else abs(float(s_mid))
    out_ft = 0.0 if out_m is None else float(out_m)
    threshold = 0 if threshold_ft is None else int(round(float(threshold_ft)))
    return {
        "side": side_text(None if s_mid is None else float(s_mid)),
        "s_ft": s_ft,
        "out_ft": out_ft,
        "what": what_text(target_class),
        "threshold_ft": threshold,
    }


def _member_actions(scale: dict | None) -> list[str]:
    if scale is None:
        return []
    unverified = scale.get("status") == "unverified" or bool(scale.get("conflict"))
    if not unverified:
        return []
    estimates = scale.get("estimates") or []
    registered = any(item.get("used") and item.get("kind") in {"meter_model", "meter_generic"} for item in estimates)
    if not scale.get("model") or not registered:
        return ["retake_meter_closeups"]
    return ["truedepth_meter_scan"]


def _scale_summary(scale: dict | None) -> ScaleSummary:
    if not scale:
        return ScaleSummary(source="none", status="unverified", relBound95=0.5)
    source = scale.get("source") if scale.get("source") in {"meter_model", "lidar", "arkit", "none"} else "none"
    status = (
        scale.get("status") if scale.get("status") in {"verified", "single_reference", "unverified"} else "unverified"
    )
    return ScaleSummary(source=source, status=status, relBound95=float(scale.get("rel_bound_95", 0.5)))


def _judge_flags(report: dict | None, verdict_dir: Path) -> list:
    if report and report.get("judge_flags"):
        return list(report["judge_flags"])
    doc = _read_json(verdict_dir / "c3.judge.json") or {}
    flags = doc.get("flags") or doc.get("judge_flags") or []
    return list(flags)


def _signed_url(storage: object, settings: object, key: str) -> str | None:
    """A time-limited download URL, or None when signing is unavailable (offline tests, CLI)."""

    sign = getattr(storage, "signed_get_url", None)
    if not callable(sign):
        return None
    ttl = getattr(settings, "HSS_INGEST_RESULT_URL_TTL_S", 900)
    try:
        return str(sign(key, int(ttl) if ttl else 900))
    except Exception:
        return None


def _review_context(
    capture,
    run,
    verdicts,
    profile,
    report,
    scene,
    response,
    internal,
    meter: MeterBlock,
    reads,
    storage=None,
    settings=None,
):
    profiles = []
    for name in ("A", "B", "C", "S"):
        doc = verdicts.get(name)
        profiles.append(
            {
                "name": name,
                "decision": None if doc is None else doc["site"]["decision"],
                "device": name == profile,
            }
        )
    photos = []
    meter_ids = set()
    if reads and reads.get("meter"):
        meter_ids.update(reads["meter"].get("images") or [])
    if reads and reads.get("confirm") and reads["confirm"].get("still_id"):
        meter_ids.add(reads["confirm"]["still_id"])
    for kf in scene.get("keyframes") or []:
        # Keyframe image names are packet-relative; the bytes a reviewer can open live
        # under the capture's promoted prefix. Sign what exists; keep the raw name
        # otherwise so an offline render still names the file.
        image = kf.get("image")
        url = None
        if image:
            photo_key = f"captures/{capture.capture_id}/{image}"
            if storage is not None and getattr(storage, "exists", lambda _k: False)(photo_key):
                url = _signed_url(storage, settings, photo_key)
        photos.append(
            {
                "id": kf["id"],
                "url": url or image or kf["id"],
                "purpose": "meter close-up" if kf["id"] in meter_ids else "keyframe",
            }
        )
    photos.sort(key=lambda item: (item["purpose"] != "meter close-up", item["id"]))
    raw = verdicts.get(profile) or {}
    return {
        "capture_id": capture.capture_id,
        "run_id": run.run_id,
        "profiles": profiles,
        "true_decision": internal["decision"],
        "failure_code": run.failure_code,
        "degraded": list(run.degraded),
        "scale": response.scale.model_dump(mode="json") if response.scale else None,
        "timings": (report or {}).get("timings_s") or {},
        "vlm_cost": ((report or {}).get("c3") or {}).get("cost_usd"),
        "decidability": internal.get("decidability"),
        "stand_in_notice": stand_in_notice(internal.get("standIns") or []),
        "photos": photos,
        "criteria": raw.get("criteria") or [],
        "walls": _wall_strips(scene, raw),
        "views": [view.model_dump(mode="json") for view in response.outcome.viewsNeeded] if response.outcome else [],
        "meter": meter.model_dump(mode="json"),
        "profile": profile,
        "links": _review_links(capture, run, verdicts, storage, settings),
    }


def _review_links(capture, run, verdicts, storage, settings) -> list[str]:
    """Reviewer links. Bare names offline; signed download URLs where storage can sign.

    The page is served at /v1/captures/{id}/review, and the API has no route for
    relative artifact names, so a bare name is a dead link whenever storage is
    available. result.json and result.internal.json are written by this stage but
    published with the run, which must land before anyone can open this page.
    """

    names = [f"verdict.{name}.json" for name in ("A", "B", "C", "S") if name in verdicts]
    names += ["criteria_report.json", "result.json", "result.internal.json"]
    prefix = f"captures/{capture.capture_id}/runs/{run.run_id}"
    keys = [f"{prefix}/verdicts/verdict.{name}.json" for name in ("A", "B", "C", "S") if name in verdicts]
    keys += [
        f"{prefix}/verdicts/criteria_report.json",
        f"{prefix}/result/result.json",
        f"{prefix}/result/result.internal.json",
    ]
    links = []
    for name, key in zip(names, keys, strict=False):
        links.append(_signed_url(storage, settings, key) or name)
    return links


def _wall_strips(scene: dict, verdict: dict) -> list[dict]:
    walls = []
    by_id = {wall["id"]: wall for wall in scene.get("walls") or []}
    openings = []
    for obj in scene.get("objects") or []:
        geom = obj.get("geometry") or {}
        if geom.get("kind") == "wall_rect" and obj.get("class") in {"window", "door", "garage_door"}:
            openings.append({"wall": geom["wall_id"], "s0": geom["s_m"][0], "s1": geom["s_m"][1]})
    wanted = {cand.get("wall_id") for cand in verdict.get("candidates") or []} or set(by_id)
    for wall_id in by_id:
        if wall_id not in wanted and verdict.get("candidates"):
            continue
        wall = by_id[wall_id]
        span = wall.get("s_range_m") or [0, 1]
        runs = []
        for cand in verdict.get("candidates") or []:
            if cand.get("wall_id") != wall_id:
                continue
            runs.append({"s0": cand["start_s_m"][0], "s1": cand["start_s_m"][1], "outcome": cand.get("outcome")})
        walls.append(
            {
                "id": wall_id,
                "s0": span[0],
                "s1": span[1],
                "runs": runs,
                "openings": [op for op in openings if op["wall"] == wall_id],
            }
        )
    return walls


def _dumps(payload: dict) -> str:
    return json.dumps(payload, indent=1, ensure_ascii=False, allow_nan=False) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compose a capture result")
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--verdicts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reads", type=Path)
    parser.add_argument("--scale", type=Path)
    parser.add_argument("--catalogue", type=Path)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--show-reject", action="store_true")
    parser.add_argument("--impl", type=Path, help="directory of impl/<stage>.json markers the orchestration published")
    args = parser.parse_args(argv)
    config = load_config()
    if args.profile:
        config = ResultConfig(**{**config.__dict__, "profile": args.profile})
    if args.show_reject:
        config = ResultConfig(**{**config.__dict__, "show_reject": True})
    if args.catalogue:
        config = ResultConfig(**{**config.__dict__, "catalogue_path": args.catalogue})
    catalogue, _missing = _catalogue(config.catalogue_path)
    capture_raw = json.loads(args.capture.read_text())
    epochs = capture_raw.pop("epochs", None)
    capture = CaptureRecord.model_validate(_snake(capture_raw))
    run_rec = RunRecord.model_validate(_snake(json.loads(args.run.read_text())))
    compose_result(
        capture,
        run_rec,
        args.scene,
        args.verdicts,
        args.out,
        reads=args.reads,
        scale=args.scale,
        catalogue=catalogue,
        config=config,
        epochs=epochs,
        impl_dir=args.impl,
    )


def _snake(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        snake = []
        for ch in key:
            if ch.isupper():
                snake.append("_")
                snake.append(ch.lower())
            else:
                snake.append(ch)
        out["".join(snake).lstrip("_")] = value
    return out


if __name__ == "__main__":
    main()
