"""Scale stage. A missing scale source degrades to a valid block; it does not raise."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from server.common.geometry import pixel_ray_arkit
from server.contracts.jobs import StageContext, StageResult
from server.contracts.scale import ArkitGateRecord, ScaleBlock, ScaleEstimate, ScaleEvidence
from server.scale.apply import scale_about
from server.scale.arkit_gate import _profile, arkit_gate
from server.scale.block import GateView, decide
from server.scale.checks import check_estimates, object_sources
from server.scale.contour import fit_meter_contours, label_contours, write_overlay
from server.scale.fuse import Source, conflict, fuse
from server.scale.lidar import align_lidar, frame_depth_spec, read_depth
from server.scale.meter_table import MeterTable, TableMatch, TableMiss
from server.scale.packet_view import (
    ScaleError,
    load_cameras,
    load_packet,
    load_points,
    load_registration,
    load_tracks,
    require,
)
from server.scale.params import ScaleParams
from server.scale.reads_view import IdentityMiss, MeterIdentity, load_meter_identity
from server.scale.rim import (
    WALK_REL_SD,
    CoverMeasurement,
    _axis_depth,
    _fit_plane,
    _ray_plane,
    _track_points,
    _wall_band,
    diameter_over,
    measure_cover,
)

_PENDING_KEYS = ("manufacturer", "model_family", "type_code", "cover_material", "images")


def run_scale(
    packet,
    root: Path,
    poses_dir: Path,
    out_dir: Path,
    *,
    params: ScaleParams | None = None,
    reads_json: Path | None = None,
    boxes_px: dict[str, list[float]] | None = None,
    early_json: Path | None = None,
) -> ScaleBlock:
    started = time.perf_counter()
    params = params or ScaleParams.from_env()
    payload, base = load_packet(packet, root)
    require(payload, "session")
    cameras = load_cameras(poses_dir)
    report = load_registration(poses_dir)
    points = load_points(poses_dir)
    tracks = load_tracks(poses_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    boxes_px = _resolve_meter_boxes(payload, cameras, poses_dir, boxes_px)

    lidar = _lidar(payload, base, cameras, points, params)
    identity, table, meter, detail, evidence_images = _meter(
        payload, base, poses_dir, cameras, report, points, tracks, params, reads_json, early_json, boxes_px, out_dir
    )
    detail = _lidar_miss_unless_meter_scaled(detail, meter, lidar)
    metric_scale = lidar.scale if lidar is not None and lidar.applied else None
    if metric_scale is None and meter is not None and meter.code is None:
        metric_scale = meter.scale
    gate = arkit_gate(
        cameras,
        payload,
        base,
        metric_scale if metric_scale is not None else _arkit_only_scale(cameras, payload, base, report, params),
        band_pct=params.arkit_band_pct,
        band_pct_2018=params.arkit_band_pct_2018,
        report=report,
    )
    # The residual is against the pre-fusion metric scale. An ARKit-only
    # session has no separate metric scale, so the gate is not a pass.
    if metric_scale is None:
        gate.passed = False
        if gate.available:
            gate.residual_pct = None
    match = table if isinstance(table, TableMatch) else None
    measured_objects = object_sources(payload, cameras, tracks)
    source_name, scale_factor, rel_bound, rel_sd, estimates = _fuse_block(
        lidar,
        meter,
        gate,
        params,
        match,
        _truedepth_source(payload, base, meter, enabled=params.truedepth_fuse),
    )
    if source_name == "none":
        detail = "no_scale_source"
        scale_factor = None
    estimates.extend(
        check_estimates(
            payload,
            footprint_enabled=params.footprint_check,
            measured_kinds={kind for kind, _source in measured_objects},
        )
    )
    conflict_flag, conflict_gap = _objects_conflict(lidar, meter, gate, params, match, measured_objects)
    if conflict_flag and conflict_gap is not None:
        rel_bound = max(rel_bound, conflict_gap)
    origin = _origin(lidar, cameras)
    lone = bool(meter is not None and getattr(meter, "lone", False))
    method = meter.method if meter is not None and meter.code is None else None
    prov = table.cover_od_prov if isinstance(table, TableMatch) else None
    agree = isinstance(identity, MeterIdentity) and identity.agree
    identity_source = identity.source if isinstance(identity, MeterIdentity) else None
    if isinstance(identity, MeterIdentity) and (not identity.agree or identity.source == "on_device_text"):
        method = method
    decision = decide(
        source=source_name,
        rel_bound_95=rel_bound,
        reason_detail=detail,
        gate=GateView(gate.available, gate.passed),
        conflict_flag=conflict_flag,
        params=params,
        meter_method=method,
        meter_prov=prov,
        lone=lone,
        agree=agree,
        identity_source=identity_source,
        pairs=int(lidar.pairs) if lidar is not None else 0,
        frames=int(lidar.frames) if lidar is not None else 0,
        metric=metric_scale is not None and source_name in {"lidar", "meter_model"},
    )
    block = ScaleBlock(
        source=source_name,  # type: ignore[arg-type]
        rel_bound_95=float(rel_bound),
        status=decision.status,  # type: ignore[arg-type]
        estimates=estimates,
        conflict=conflict_flag,
        model=_model_name(identity, table),
        scale_factor=None if source_name == "none" else scale_factor,
        origin_m=None if origin is None else [float(v) for v in origin],
        source_frame=report.get("source_frame"),
        rel_sd=rel_sd,
        confidence=decision.confidence,
        reason=decision.reason,
        reason_detail=decision.reason_detail,
        table_entry=table.model_id if isinstance(table, TableMatch) else None,
        table_version=_table_version(params),
        arkit_gate=ArkitGateRecord(
            available=gate.available,
            passed=gate.passed,
            profile=gate.profile,
            band_pct=gate.band_pct,
            residual_pct=gate.residual_pct,
            rel_sd=gate.rel_sd,
            s_arkit_per_source=gate.s_arkit_per_source,
        ),
        evidence=ScaleEvidence(images=evidence_images, boxes_px=boxes_px or {}),
        views_needed=decision.views_needed,
        member_actions=decision.member_actions,
        timings_s={"total": time.perf_counter() - started},
    )
    _write(out_dir, block)
    return block


def _lidar(packet, root, cameras, points, params):
    if (packet.get("session") or {}).get("tier") != "arkit_lidar":
        return None
    depths = {}
    saw_spec = False
    for frame in packet.get("keyframes") or []:
        spec = frame_depth_spec(frame, root)
        if spec is None:
            continue
        saw_spec = True
        pair = read_depth(spec)
        if pair is None:
            continue
        depths[str(frame.get("id"))] = pair
    if not saw_spec:
        return None
    if not depths:
        alignment = align_lidar(
            points, [], {}, min_conf=2, min_pairs=params.lidar_min_pairs, max_m=params.lidar_max_m, seed=params.seed
        )
        alignment.reason = "lidar_no_depth_files"
        alignment.applied = False
        return alignment
    usable = [camera for camera in cameras if camera.image in depths or camera.keyframe_id in depths]
    renamed = {}
    for camera in usable:
        renamed[camera.image] = depths.get(camera.image) or depths.get(camera.keyframe_id)
    return align_lidar(
        points,
        usable,
        {key: value for key, value in renamed.items() if value is not None},
        min_conf=2,
        min_pairs=params.lidar_min_pairs,
        max_m=params.lidar_max_m,
        seed=params.seed,
        verified_pairs=params.lidar_verified_pairs,
        min_m=params.lidar_min_m,
    )


def _meter(packet, root, poses_dir, cameras, report, points, tracks, params, reads_json, early_json, boxes_px, out_dir):
    close = (packet.get("scaleReference") or {}).get("meterCloseUp") or {}
    if close.get("captured") is False or not packet.get("scaleReference"):
        return None, None, None, "meter_closeup_missing", []
    early = early_json
    if early is None:
        candidate = root / "early" / "meter_identity.json"
        early = candidate if candidate.is_file() else None
    identity = load_meter_identity(reads_json, packet, early_json=early, min_confidence=params.identity_min_confidence)
    if isinstance(identity, IdentityMiss):
        return identity, None, None, identity.code, []
    table = _load_table(params)
    if table is None:
        _pending(out_dir, identity)
        return identity, None, None, "meter_model_not_in_table", list(identity.images)
    looked = table.lookup(identity, allow_assumed=params.allow_assumed_dimensions)
    if isinstance(looked, TableMiss):
        _pending(out_dir, identity)
        return identity, looked, None, looked.code, list(identity.images)
    registered = _registered_closeups(report, cameras)
    _, arkit_sd, _ = _profile(packet.get("session") or {}, params.arkit_band_pct, params.arkit_band_pct_2018)
    if not registered:
        walked = _walk_frame(packet, root, cameras, boxes_px, tracks, looked, params, arkit_sd)
        if walked is None:
            return identity, looked, None, "meter_closeup_unregistered", list(identity.images)
        return identity, looked, walked, None, list(identity.images)
    measurement, images = _measure_closeups(
        packet, root, registered, looked, points, tracks, boxes_px, out_dir, params, arkit_sd
    )
    if measurement is None or measurement.code in {
        "scale_reference_edge_not_found",
        "meter_contour_ambiguous",
        "scale_plane_fit_failed",
    }:
        code = measurement.code if measurement is not None and measurement.code else "scale_reference_edge_not_found"
        return identity, looked, None, code, images or list(identity.images)
    if measurement.code:
        return identity, looked, measurement, measurement.code, images
    return identity, looked, measurement, None, images


_METER_MISS = {
    "meter_closeup_missing",
    "meter_closeup_unregistered",
    "meter_model_unread",
    "meter_identity_low_confidence",
    "meter_model_not_in_table",
    "meter_dimension_assumed",
    "meter_contour_ambiguous",
    "scale_reference_edge_not_found",
    "scale_reference_views_disagree",
    "scale_plane_fit_failed",
}


def _lidar_miss_unless_meter_scaled(detail, meter, lidar):
    """A finished meter scale is the fallthrough. A LiDAR miss must not replace it.

    The reverse is also true: once LiDAR produced a scale, a missing meter
    close-up is only an absent check. It must not mark the house unresolved.
    """

    if lidar is not None and lidar.applied and detail in _METER_MISS:
        return None
    if meter is not None and meter.code is None:
        return detail
    if detail is None and lidar is not None and not lidar.applied and lidar.reason:
        return lidar.reason
    return detail


def _measure_closeups(packet, root, registered, match, points, tracks, boxes_px, out_dir, params, arkit_sd):
    views = []
    images = []
    ambiguous = False
    for camera, contours_image in registered:
        gray = _load_gray(root, packet, contours_image)
        if gray is None:
            continue
        box = (boxes_px or {}).get(camera.image) or (boxes_px or {}).get(contours_image)
        ellipses = fit_meter_contours(gray, box, seed=params.seed)
        hint = _implied_diameter_m(ellipses, camera, packet, contours_image)
        labelled = label_contours(ellipses, match, hint, arkit_rel_sd=float(arkit_sd))
        write_overlay(out_dir / f"contour_overlay_{Path(contours_image).stem}.jpg", gray, labelled, box)
        images.append(camera.image)
        if labelled.code == "meter_contour_ambiguous":
            ambiguous = True
            continue
        if labelled.code:
            continue
        views.append((camera, labelled))
    if not views:
        if ambiguous:
            return _ambiguous_measurement(), images
        return None, images
    measured = measure_cover(views, match, points, tracks)
    measured.lone = any(contours.lone for _, contours in views)
    return measured, images


def _ambiguous_measurement() -> CoverMeasurement:
    return CoverMeasurement(
        "two_view",
        1.0,
        0.0,
        0.0,
        np.zeros(3),
        np.zeros(3),
        [],
        [],
        0.0,
        None,
        "meter_contour_ambiguous",
        [],
        True,
    )


def _implied_diameter_m(ellipses, camera, packet, image: str) -> float | None:
    """Metres the lone ellipse would have at the close-up's ARKit distance."""

    if not ellipses:
        return None
    distance = _arkit_distance_m(packet, image) or _arkit_distance_m(packet, camera.image)
    focal = float(camera.intrinsics[0])
    if distance is None or focal <= 0:
        return None
    ellipse = min(ellipses, key=lambda item: item.a)
    return 2.0 * float(ellipse.a) * distance / focal


def _arkit_distance_m(packet: dict, image: str) -> float | None:
    ref = packet.get("scaleReference") or {}
    rows = [ref.get("meterCloseUp") or {}]
    ext = ref.get("ext") or {}
    if isinstance(ext, dict) and isinstance(ext.get("obliqueCloseUp"), dict):
        rows.append(ext["obliqueCloseUp"])
    for row in rows:
        if image not in {row.get("still"), row.get("frame"), row.get("id")}:
            continue
        distance = row.get("arkitDistanceM")
        if isinstance(distance, (int, float)) and float(distance) > 0:
            return float(distance)
    return None


def _walk_frame(packet, root, cameras, boxes_px, tracks, match, params, arkit_sd) -> CoverMeasurement | None:
    """Cover in the widest registered frame that shows the meter, depth from the wall."""

    if not isinstance(match, TableMatch):
        return None
    best = None
    for camera in cameras:
        box = (boxes_px or {}).get(camera.image) or (boxes_px or {}).get(camera.keyframe_id or "")
        if not box or len(box) < 4:
            continue
        width = abs(float(box[2]) - float(box[0]))
        if width < 40:
            continue
        if best is None or width > best[0]:
            best = (width, camera, [float(value) for value in box[:4]])
    if best is None:
        return None
    _width, camera, box = best
    gray = _load_gray(root, packet, camera.image)
    if gray is None:
        return None
    ellipses = fit_meter_contours(gray, box, seed=params.seed)
    hint = _implied_diameter_m(ellipses, camera, packet, camera.image)
    if hint is None:
        hint = _tap_implied_diameter(ellipses, camera, packet)
    labelled = label_contours(ellipses, match, hint, arkit_rel_sd=float(arkit_sd))
    cover = labelled.cover
    if cover is None:
        return None
    cloud = _wall_band(_track_points(tracks, camera.image), camera, cover)
    plane = _fit_plane(cloud)
    if plane is None:
        return None
    origin, direction = pixel_ray_arkit(camera.pose, camera.intrinsics, np.array([cover.cx, cover.cy]))
    point = _ray_plane(origin, direction, plane)
    if point is None:
        return None
    depth = _axis_depth(camera, point)
    focal = float(camera.intrinsics[0])
    if depth <= 0.05 or focal <= 0 or cover.a <= 0:
        return None
    scale = diameter_over(match) * focal / (2 * cover.a * depth)
    rim = float(match.rim_offset_m or 0.0)
    z_cover = depth - rim / scale if scale > 1e-9 else depth
    if z_cover <= 0.05:
        return None
    return CoverMeasurement(
        "walk_frame",
        float(scale),
        WALK_REL_SD,
        float(cover.a * z_cover / focal),
        point,
        plane[1],
        [],
        [float(scale)],
        0.0,
        None,
        None,
        [],
        depth_source=float(z_cover),
    )


def _truedepth_source(packet, root: Path, meter, *, enabled: bool = True) -> Source | None:
    """Cover diameter from the front-camera scan, divided by the model's cover diameter.

    The scan has no world pose, so a depth ratio against the close-up or the wall
    is not a scale. Each frame contributes ``2 a z / f`` inside its own cover ellipse.
    """

    if not enabled:
        return None
    scan = ((packet.get("scaleReference") or {}).get("ext") or {}).get("trueDepthScan") or {}
    if scan.get("depthDataAccuracy") != "absolute" or scan.get("depthDataQuality") != "high":
        return None
    frames = scan.get("frames") or []
    if not scan.get("captured") or len(frames) < 10:
        return None
    radius = getattr(meter, "radius_source", None) if meter is not None else None
    if not isinstance(radius, (int, float)) or float(radius) <= 0:
        return None
    diameters = []
    for frame in frames:
        diameter = _truedepth_diameter(root, frame)
        if diameter is not None and diameter > 0:
            diameters.append(diameter)
    if len(diameters) < 10:
        return None
    scale = float(np.median(diameters)) / (2.0 * float(radius))
    if scale <= 0:
        return None
    return Source("truedepth", float(np.log(scale)), 0.015)


def _truedepth_diameter(root: Path, frame: dict) -> float | None:
    width, height = int(frame.get("w") or 0), int(frame.get("h") or 0)
    intrinsics = frame.get("intrinsics") or []
    image_name = frame.get("image")
    if width < 8 or height < 8 or len(intrinsics) < 1 or not image_name:
        return None
    focal = float(intrinsics[0])
    depth_path = root / str(frame.get("depth") or "")
    gray = cv2.imread(str(root / str(image_name)), cv2.IMREAD_GRAYSCALE)
    if focal <= 0 or gray is None or not depth_path.is_file() or depth_path.stat().st_size != width * height * 4:
        return None
    depth = np.fromfile(depth_path, dtype="<f4", count=width * height).reshape(height, width)
    ellipses = fit_meter_contours(gray, None, seed=0)
    if not ellipses:
        return None
    cover = max(ellipses, key=lambda item: item.a)
    sampled = _median_depth_in_ellipse(depth, cover)
    if sampled is None:
        return None
    return 2.0 * float(cover.a) * sampled / focal


def _median_depth_in_ellipse(depth: np.ndarray, cover) -> float | None:
    height, width = depth.shape
    ys, xs = np.mgrid[0:height, 0:width]
    theta = np.deg2rad(float(cover.theta_deg))
    du = xs - float(cover.cx)
    dv = ys - float(cover.cy)
    ellipse_x = np.cos(theta) * du + np.sin(theta) * dv
    ellipse_y = -np.sin(theta) * du + np.cos(theta) * dv
    radius = (ellipse_x / max(float(cover.a), 1e-6)) ** 2 + (ellipse_y / max(float(cover.b), 1e-6)) ** 2
    good = (radius <= 1.0) & np.isfinite(depth) & (depth > 0.15) & (depth < 5.0)
    if int(good.sum()) < 10:
        return None
    return float(np.median(depth[good]))


_METER_BOX_CLASSES = {"meter_can", "electric_meter"}


def _resolve_meter_boxes(packet, cameras, poses_dir: Path, boxes_px) -> dict[str, list[float]]:
    """WS-07 boxes, else the meter tap at ±0.5 m. An explicit argument wins."""

    resolved: dict[str, list[float]] = {}
    resolved.update(_boxes_from_objects(poses_dir))
    for image, box in _boxes_from_taps(packet, cameras).items():
        resolved.setdefault(image, box)
    for image, box in (boxes_px or {}).items():
        resolved[str(image)] = [float(value) for value in box[:4]]
    return resolved


def _boxes_from_objects(poses_dir: Path) -> dict[str, list[float]]:
    path = Path(poses_dir).parent / "objects" / "objects.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    boxes: dict[str, list[float]] = {}
    for obj in payload.get("objects") or []:
        if obj.get("class") not in _METER_BOX_CLASSES:
            continue
        for view in obj.get("views") or []:
            box = view.get("box_px")
            image = view.get("image")
            if image and isinstance(box, list) and len(box) >= 4:
                boxes[str(image)] = [float(value) for value in box[:4]]
    return boxes


def _boxes_from_taps(packet, cameras) -> dict[str, list[float]]:
    by_key = {}
    for camera in cameras:
        by_key[camera.image] = camera
        if camera.keyframe_id:
            by_key.setdefault(camera.keyframe_id, camera)
    boxes: dict[str, list[float]] = {}
    for tap in packet.get("taps") or []:
        if tap.get("label") != "meter":
            continue
        camera = by_key.get(tap.get("keyframe"))
        pixel = tap.get("pixel") or []
        if camera is None or len(pixel) < 2:
            continue
        hit = tap.get("hit") or {}
        distance = hit.get("distance")
        if not isinstance(distance, (int, float)) or float(distance) <= 0:
            distance = _arkit_distance_m(packet, camera.image)
        focal = float(camera.intrinsics[0])
        if not isinstance(distance, (int, float)) or float(distance) <= 0 or focal <= 0:
            continue
        half = focal * 0.5 / float(distance)
        u, v = float(pixel[0]), float(pixel[1])
        box = [u - half, v - half, u + half, v + half]
        boxes[camera.image] = box
        if camera.keyframe_id:
            boxes.setdefault(camera.keyframe_id, box)
    return boxes


def _tap_implied_diameter(ellipses, camera, packet) -> float | None:
    if not ellipses:
        return None
    distance = None
    for tap in packet.get("taps") or []:
        if tap.get("label") != "meter":
            continue
        if tap.get("keyframe") not in {camera.image, camera.keyframe_id}:
            continue
        hit = tap.get("hit") or {}
        if isinstance(hit.get("distance"), (int, float)) and float(hit["distance"]) > 0:
            distance = float(hit["distance"])
            break
    focal = float(camera.intrinsics[0])
    if distance is None or focal <= 0:
        return None
    ellipse = min(ellipses, key=lambda item: item.a)
    return 2.0 * float(ellipse.a) * distance / focal


def _fuse_block(lidar, meter, gate, params, match: TableMatch | None, truedepth: Source | None = None):
    estimates: list[ScaleEstimate] = []
    sources: list[Source] = []
    lidar_ok = lidar is not None and lidar.applied
    meter_ok = meter is not None and meter.code in {None, "scale_reference_views_disagree"}
    if lidar_ok:
        sources.append(Source("lidar", float(np.log(lidar.scale)), max(lidar.rel_sd, params.lidar_sd_floor)))
        estimates.append(
            ScaleEstimate(
                kind="lidar",
                rel_sd=float(lidar.rel_sd),
                used=True,
                role="primary",
                method="lidar_ratio",
                pairs=int(lidar.pairs),
            )
        )
    elif lidar is not None and not lidar.applied and lidar.reason == "lidar_too_few_pairs":
        # The meter miss stays on reason_detail. The LiDAR miss is its own estimate.
        estimates.append(
            ScaleEstimate(
                kind="lidar",
                rel_sd=float(lidar.rel_sd),
                used=False,
                method="lidar_ratio",
                pairs=int(lidar.pairs),
                reason="lidar_too_few_pairs",
            )
        )
    if meter_ok:
        meter_sd = _meter_sd(meter, match)
        if not lidar_ok:
            sources.append(_meter_source(meter, match, meter_sd))
        estimates.append(
            ScaleEstimate(
                kind="meter_model",
                rel_sd=meter_sd,
                used=not lidar_ok,
                role="primary" if not lidar_ok else "check",
                method=meter.method,
                contour="cover",
            )
        )
    if gate.available and gate.s_arkit_per_source:
        sources.append(
            Source("arkit", float(np.log(gate.s_arkit_per_source)), float(gate.rel_sd or 0.03), student=True)
        )
        estimates.append(
            ScaleEstimate(
                kind="arkit_poses",
                rel_sd=float(gate.rel_sd or 0.03),
                used=True,
                method="umeyama",
                residual_pct=gate.residual_pct,
            )
        )
    if truedepth is not None:
        sources.append(truedepth)
        estimates.append(ScaleEstimate(kind="truedepth", rel_sd=0.015, used=True, method="truedepth"))
    if not sources:
        return "none", None, 1.0, None, estimates
    posterior = fuse(sources)
    if lidar_ok:
        source = "lidar"
    elif meter_ok:
        source = "meter_model"
    elif gate.available:
        source = "arkit"
    else:
        source = "none"
    rel_sd = posterior.bound_95 / (1.1 * 1.96) if posterior.bound_95 else None
    return source, posterior.scale if source != "none" else None, posterior.bound_95, rel_sd, estimates


def _meter_sd(meter, match: TableMatch | None) -> float:
    sd = max(float(meter.rel_sd), 1e-4)
    if match is not None and match.match == "fuzzy":
        sd *= 1.5
    return sd


def _meter_source(meter, match: TableMatch | None, sd: float) -> Source:
    """Reference diameter is hypotheses[0]. Other covers shift by log(D_ref / D_i)."""

    observed = float(np.log(max(meter.scale, 1e-6)))
    hypotheses = list(match.cover_hypotheses) if match is not None else []
    if len(hypotheses) < 2:
        return Source("meter", observed, sd)
    reference = float(hypotheses[0][0])
    offsets = np.array([np.log(reference / float(diameter)) for diameter, _weight in hypotheses])
    weights = np.array([float(weight) for _diameter, weight in hypotheses])
    return Source("meter", observed, sd, offsets=offsets, weights=weights)


def _arkit_only_scale(cameras, packet, root, report, params) -> float | None:
    gate = arkit_gate(
        cameras,
        packet,
        root,
        1.0,
        band_pct=params.arkit_band_pct,
        band_pct_2018=params.arkit_band_pct_2018,
        report=report,
    )
    return gate.s_arkit_per_source


def _objects_conflict(lidar, meter, gate, params, match, measured) -> tuple[bool, float | None]:
    """Checks posterior is ARKit plus the door and garage sources. It never moves the scale."""

    if not measured:
        return False, None
    anchor_sources = []
    if lidar is not None and lidar.applied:
        anchor_sources.append(Source("lidar", float(np.log(lidar.scale)), max(lidar.rel_sd, params.lidar_sd_floor)))
    elif meter is not None and meter.code is None:
        anchor_sources.append(_meter_source(meter, match, _meter_sd(meter, match)))
    if not anchor_sources:
        return False, None
    check_sources = []
    if gate.available and gate.s_arkit_per_source:
        check_sources.append(
            Source("arkit", float(np.log(gate.s_arkit_per_source)), float(gate.rel_sd or 0.03), student=True)
        )
    check_sources.extend(source for _kind, source in measured)
    anchor = fuse(anchor_sources)
    checks = fuse(check_sources)
    if not conflict(anchor, checks):
        return False, None
    return True, abs(anchor.log_est - checks.log_est)


def _origin(lidar, cameras):
    if lidar is not None and lidar.origin is not None:
        return np.asarray(lidar.origin, float)
    if not cameras:
        return None
    return np.mean([camera.pose[:3, 3] for camera in cameras], axis=0)


def _model_name(identity, table) -> str | None:
    if isinstance(table, TableMatch):
        return table.model_id
    if isinstance(identity, MeterIdentity):
        return identity.model_family
    return None


def _table_version(params: ScaleParams) -> str | None:
    path = params.table_path
    if not path.is_file():
        return None
    try:
        return str(json.loads(path.read_text()).get("table_version"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_table(params: ScaleParams) -> MeterTable | None:
    if not params.table_path.is_file():
        return None
    return MeterTable.load(params.table_path)


def _pending(out_dir: Path, identity: MeterIdentity) -> None:
    row = {key: getattr(identity, key) for key in _PENDING_KEYS}
    with (out_dir / "meter_models.pending.jsonl").open("a") as handle:
        handle.write(json.dumps(row) + "\n")


def _registered_closeups(report: dict, cameras: list):
    by_image = {camera.image: camera for camera in cameras}
    found = []
    closeups = report.get("closeups") or {}
    for purpose in ("meter_close", "meter_oblique"):
        row = closeups.get(purpose) or {}
        if not row.get("registered"):
            continue
        frame = str(row.get("frame") or "")
        camera = by_image.get(frame)
        if camera is not None:
            found.append((camera, frame))
    return found


def _load_gray(root: Path, packet: dict, image: str):
    stills = packet.get("stills") or []
    keyframes = packet.get("keyframes") or []
    relative = None
    for still in stills:
        if still.get("id") == image or still.get("img") == image or still.get("purpose") == image:
            relative = still.get("img")
            break
    if relative is None:
        for frame in keyframes:
            if frame.get("id") == image:
                relative = frame.get("img") or frame.get("file")
                break
    if relative is None:
        relative = image
    path = root / relative
    if not path.is_file():
        return None
    image_bgr = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    return image_bgr


def _write(out_dir: Path, block: ScaleBlock) -> None:
    (out_dir / "scale.json").write_text(block.model_dump_json(indent=2) + "\n")
    report = {
        "total_s": (block.timings_s or {}).get("total", 0.0),
        "source": block.source,
        "reason_detail": block.reason_detail,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")


def emit_metric_copies(block: ScaleBlock, points, cameras, out_dir: Path) -> None:
    if block.scale_factor is None or block.origin_m is None:
        return
    scaled_points, scaled_cameras = scale_about(points, cameras, block.scale_factor, np.asarray(block.origin_m, float))
    _write_ply(out_dir / "sparse_metric.ply", scaled_points)
    rows = []
    for camera in scaled_cameras:
        rows.append(
            {
                "image": camera.image,
                "keyframe_id": camera.keyframe_id,
                "pose": camera.pose.tolist(),
                "intrinsics": camera.intrinsics,
                "w": camera.w,
                "h": camera.h,
            }
        )
    (out_dir / "cameras_metric.json").write_text(json.dumps(rows) + "\n")


def _write_ply(path: Path, points: np.ndarray) -> None:
    cloud = np.asarray(points, np.float32)
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(cloud)}\nproperty float x\nproperty float y\nproperty float z\nend_header\n"
    )
    path.write_bytes(header.encode("ascii") + cloud.tobytes())


def _work_dir_input(ctx: StageContext, key: str) -> Path | None:
    """A WS-00 §3.9 artifact in the stage work dir, else the same key on ``ctx.inputs``.

    WS-03 copies the current run under ``work_dir`` (``reads/reads.json``,
    ``early/meter_identity.json``). ``ctx.inputs`` only carries ``packet_dir``,
    ``run_control``, and ``parent/<key>``.
    """

    staged = Path(ctx.work_dir) / key
    if staged.is_file():
        return staged
    given = ctx.inputs.get(key)
    if given and Path(given).is_file():
        return Path(given)
    return None


def _poses_from_work_dir(work_dir: Path) -> Path:
    """WS-04's pose artifacts, as named in WS-00 §3.9, under the stage work dir.

    ``poses/cameras.json``, ``poses/sparse.ply``, ``poses/tracks.npz``, and
    ``poses/registration_report.json``. WS-03 copies those keys into ``work_dir``;
    they are not entries on ``ctx.inputs``.
    """

    poses_dir = Path(work_dir) / "poses"
    if not (poses_dir / "cameras.json").is_file():
        raise ScaleError("poses_artifact_missing:cameras.json")
    return poses_dir


def execute(ctx: StageContext) -> StageResult:
    """Stage entry. There is no ``identify_meter`` hook (SD-46)."""

    packet_dir = Path(ctx.inputs["packet_dir"])
    # WS-03 puts the current run's artifacts in the stage work dir under the
    # WS-00 §3.9 keys. ``ctx.inputs`` only carries packet_dir, run_control, and parent/<key>.
    poses_dir = _poses_from_work_dir(ctx.work_dir)
    reads = _work_dir_input(ctx, "reads/reads.json")
    early = _work_dir_input(ctx, "early/meter_identity.json")
    out_dir = ctx.work_dir / "scale"
    params = ScaleParams.from_env()
    if ctx.settings is not None and hasattr(ctx.settings, "scale"):
        params = ctx.settings.scale
    block = run_scale(
        packet_dir,
        packet_dir,
        poses_dir,
        out_dir,
        params=params,
        reads_json=Path(reads) if reads else None,
        early_json=Path(early) if early else None,
    )
    artifacts = {f"scale/{path.name}": str(path) for path in out_dir.iterdir() if path.is_file()}
    if ctx.storage is not None and hasattr(ctx.storage, "upload"):
        ctx.storage.upload(f"captures/{ctx.capture_id}/runs/{ctx.run_id}/scale", out_dir)
    return StageResult(
        artifacts=artifacts,
        metrics={"rel_bound_95": float(block.rel_bound_95)},
        warnings=[],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the scale stage on one packet manifest.")
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--poses", type=Path, required=True)
    parser.add_argument("--reads", type=Path, default=None)
    parser.add_argument("--early", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--emit-metric-copies", action="store_true")
    args = parser.parse_args(argv)
    block = run_scale(
        args.packet,
        args.packet if args.packet.is_dir() else args.packet.parent,
        args.poses,
        args.out,
        reads_json=args.reads,
        early_json=args.early,
    )
    if args.emit_metric_copies:
        emit_metric_copies(block, load_points(args.poses), load_cameras(args.poses), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
