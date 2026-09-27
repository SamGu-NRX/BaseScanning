"""Assemble a base-scene/v0 document from a metric cloud and registered cameras."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from server.common.rays import rays_from_tracks
from server.common.requests import views_needed
from server.common.role import COLMAP_DEPTH_P90_IN, coverage_only_dense
from server.scene.completion import complete_walls
from server.scene.contract import check_scene
from server.scene.errors import SceneError
from server.scene.features import (
    apply_pose_floors,
    clamp_angle,
    drop_glass_walls,
    drop_phantom_walls,
    refit_wall_extents,
    sort_scene_spans,
    upgrade_evidence,
)
from server.scene.ground import apply_ground_guard
from server.scene.harness import load as load_harness
from server.scene.params import SceneParams

LICENSE = "COLMAP BSD-3; Pi3 BSD-2; MoGe-2 MIT; DA3-Large Apache-2.0; Open3D MIT"


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "dev"


def scale_source_name(source: str) -> tuple[str, list[str]]:
    if source == "lidar":
        return "lidar", []
    if source == "arkit":
        return "arkit_poses", []
    if source == "meter_model":
        return "scale_reference", []
    if source == "none":
        return "none", []
    return "none", []


def pose_source_name(scale_source: str, camera_pose_source: str) -> str:
    if camera_pose_source == "arkit":
        return "arkit"
    if scale_source in {"lidar", "meter_model"}:
        return "sfm"
    if scale_source == "arkit":
        return "sfm_scaled_by_arkit"
    return "sfm"


def grid_bounds(scene: dict, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    xs, zs = [], []
    for wall in scene["walls"]:
        for p in wall["baseline_m"]:
            xs.append(p[0])
            zs.append(p[1])
    if len(points):
        zs.append(float(points[:, 2].min()))
        zs.append(float(points[:, 2].max()))
    lo = np.array([min(xs) - 4.0, -0.5, min(-2.0, min(zs) - 2.0)])
    hi = np.array([max(xs) + 4.0, 6.0, max(zs) + 16.0])
    return lo, hi


def set_observed_fractions(scene: dict, grid) -> None:
    chain = load_harness()["chain"].Chain(scene)
    by_id = {w["id"]: w for w in scene["walls"]}
    for cw in chain.walls:
        wall = by_id[cw.id]
        if wall.get("evidence") == "inferred" and int(wall.get("support", {}).get("points", 0)) == 0:
            wall["observed_fraction"] = 0.0
            continue
        ss = np.arange(cw.s0 + 0.05, cw.s1 - 0.05, 0.25)
        if len(ss) == 0:
            wall["observed_fraction"] = 0.0
            continue
        probes = []
        for s in ss:
            xz = cw.plan_at(float(s))
            for y in (0.15, 0.5, 1.0):
                probes.append([float(xz[0]), y, float(xz[1])])
        seen = grid.observed_at(np.asarray(probes, float))
        wall["observed_fraction"] = round(float(np.mean(seen)), 3)


def build_metric_scene(
    *,
    points: np.ndarray,
    src: np.ndarray,
    tracks: list[np.ndarray],
    cameras: list,
    meter_clicks: list[tuple[int, float, float]],
    scale: dict,
    packet: dict,
    source_frame: str,
    t_w_from_source: np.ndarray,
    params: SceneParams,
    flow: str,
    pose_sources: list[str],
    scene_id: str,
    warnings: list[str] | None = None,
    coverage_query_factory=None,
    depth_maps: dict | None = None,
    depth_source: str | None = None,
) -> tuple[dict, dict, list[dict]]:
    """Return ``(scene, state, views_needed)``. ``points`` are in frame W."""
    notes = list(warnings or [])
    harness = load_harness()
    extract = harness["extract"]
    frame = harness["frame"]
    coverage = harness["coverage"]
    pts = np.asarray(points, float)
    src = np.asarray(src, int)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise SceneError("input_missing:points")
    role_coverage = coverage_only_dense(source_frame, flow)
    if role_coverage:
        notes.append(f"dense_coverage_only:pi3_da3_p90_in_{COLMAP_DEPTH_P90_IN[0]:.0f}-{COLMAP_DEPTH_P90_IN[1]:.0f}")
        measure = src != 1
    else:
        measure = np.ones(len(pts), dtype=bool)
    m_pts, m_src = pts[measure], src[measure]
    if len(m_pts) < 60:
        raise SceneError("no_wall", "not enough points to fit a wall")
    m_tracks = [tracks[i] for i in np.flatnonzero(measure)]
    provenance = _provenance(packet, scale, params, flow, source_frame, scene_id, len(cameras), notes)
    try:
        scene, state = extract.extract_scene(
            m_pts,
            m_tracks,
            cameras,
            meter_clicks,
            [],
            extract.ExtractParams(
                scale_rel_pm=float(scale.get("rel_bound_95") or 0.0),
                voxel_m=params.voxel_m,
                seed=params.seed,
                min_wall_pts=60,
            ),
            provenance,
            scene_id,
            source_frame,
            t_w_from_source,
        )
    except RuntimeError as exc:
        if "meter clicks" in str(exc):
            raise SceneError("meter_not_on_wall", str(exc)) from exc
        raise
    except TypeError:
        scene, state = extract.extract_scene(
            m_pts,
            m_tracks,
            cameras,
            meter_clicks,
            [],
            extract.ExtractParams(
                scale_rel_pm=float(scale.get("rel_bound_95") or 0.0), voxel_m=params.voxel_m, seed=params.seed
            ),
            provenance,
            scene_id,
            source_frame,
            t_w_from_source,
        )
    if not scene["walls"]:
        raise SceneError("no_wall")
    scene_pts = state["points_c"]
    refit_wall_extents(scene, scene_pts)
    drop_glass_walls(scene, params, notes)
    # Phantom guard looks at source-tagged points in the scene frame.
    src_scene = _src_for_scene_points(m_pts, m_src, state)
    drop_phantom_walls(scene, scene_pts, src_scene, params, notes)
    if not scene["walls"] or not scene["chain"]["wall_ids"]:
        raise SceneError("no_wall")
    to_scene = state["T"] @ np.asarray(t_w_from_source, float)
    planes = list(packet.get("planes") or [])
    taps = list(packet.get("taps") or [])
    complete_walls(scene, planes, taps, to_scene, params, notes)
    apply_pose_floors(scene, pose_sources, params, notes)
    guard_tracks = m_tracks if len(m_tracks) == len(scene_pts) else None
    base_unobserved = apply_ground_guard(scene, scene_pts, guard_tracks, params, notes, state=state)
    _retarget_keyframes(scene, state, scale, pose_sources)
    lo, hi = grid_bounds(scene, state["points_c"])
    n_vox = int(np.prod(np.ceil((hi - lo) / params.voxel_m)))
    voxel = params.voxel_m
    if n_vox > 60_000_000:
        voxel = 0.075
        notes.append("grid_voxel_raised")
    ray_pts = state["points_c"]
    ray_src = src_scene
    ray_tracks = _tracks_in_scene(m_pts, m_tracks, ray_pts, state["T"])
    if role_coverage:
        # π³ points were withheld from the fit. Add them as coverage rays only.
        extra_idx = np.flatnonzero(src == 1)
        if len(extra_idx):
            extra_c = frame.apply(state["T"], pts[extra_idx])
            ray_pts = np.vstack([ray_pts, extra_c])
            ray_src = np.concatenate([ray_src, np.ones(len(extra_c), int)])
            ray_tracks = list(ray_tracks) + [tracks[int(i)] for i in extra_idx]
    sets = rays_from_tracks(state["cams_c"], ray_pts, ray_tracks, ray_src)
    grid = coverage.build_grid(sets, lo, hi, voxel)
    set_observed_fractions(scene, grid)
    upgrade_evidence(scene, grid, notes)
    maps = depth_maps or {}
    query = None
    if coverage_query_factory is not None:
        query = coverage_query_factory(state["cams_c"], state["points_c"], maps)
    if query is not None:
        bands = coverage.bands_from_grid(grid, scene, query=query)
        sensitivity = coverage.ground_sensitivity(grid, scene, query)
    else:
        bands = coverage.bands_from_grid(grid, scene)
        sensitivity = []
    for band in bands:
        if "best_incidence_deg" in band and band["best_incidence_deg"] is not None:
            band["best_incidence_deg"] = clamp_angle(band["best_incidence_deg"], notes)
        if "s_m" in band:
            a, b = band["s_m"]
            band["s_m"] = [a, b] if a <= b else [b, a]
    needed = views_needed(scene, bands, params, ground_base_unobserved=base_unobserved)
    relief = f"relief max({params.relief_m:.2f} m, {params.relief_rel * 100:.0f} %)"
    if maps:
        label = "lidar depth" if depth_source == "lidar" else "moge2 depth"
        method = f"rays: sfm_tracks; occlusion: blockers + {label}, {relief}"
    else:
        method = "rays: sfm_tracks; occlusion: blockers"
    scene["coverage"] = {
        "method": method,
        "bands": bands,
        "rule": {
            "min_views": 2,
            "max_incidence_deg": {"wall": 75.0, "ground": 80.0 if params.ground_rule != "angle75" else 75.0},
        },
        "sensitivity": sensitivity,
    }
    sort_scene_spans(scene)
    scene["provenance"]["notes"] = list(dict.fromkeys(notes))
    scene["provenance"]["producer"]["params"]["scale"] = dict(scale)
    scene["provenance"]["producer"]["params"]["geometry_role"] = "coverage_only" if role_coverage else "measure"
    state["grid"] = grid
    state["notes"] = scene["provenance"]["notes"]
    state["measure_src"] = src_scene
    check_scene(scene)
    return scene, state, needed


def _tracks_in_scene(
    measure_pts: np.ndarray, tracks: list[np.ndarray], scene_pts: np.ndarray, transform: np.ndarray
) -> list[np.ndarray]:
    """Camera indices for scene-frame points. A point with no match claims no camera."""
    if len(tracks) == len(scene_pts):
        return list(tracks)
    if len(scene_pts) == 0:
        return []
    if len(measure_pts) == 0 or len(tracks) != len(measure_pts):
        return [np.zeros(0, np.int32) for _ in range(len(scene_pts))]
    frame = load_harness()["frame"]
    base = np.asarray(frame.apply(np.asarray(transform, float), measure_pts), float)
    out = []
    for point in np.asarray(scene_pts, float):
        distances = np.sum((base - point) ** 2, axis=1)
        nearest = int(np.argmin(distances))
        if float(distances[nearest]) > 1e-6:
            out.append(np.zeros(0, np.int32))
        else:
            out.append(np.asarray(tracks[nearest], np.int32).ravel())
    return out


def _src_for_scene_points(measure_pts: np.ndarray, measure_src: np.ndarray, state: dict) -> np.ndarray:
    """``state['points_c']`` is every measurement point, transformed. src follows that order."""
    if len(state["points_c"]) == len(measure_src):
        return np.asarray(measure_src, int)
    return np.zeros(len(state["points_c"]), int)


def _provenance(
    packet, scale, params: SceneParams, flow: str, source_frame: str, scene_id: str, n_cams: int, notes: list[str]
) -> dict:
    source = str(scale.get("source", "none"))
    scale_name, ref_ids = scale_source_name(source)
    kind = (packet.get("source") or {}).get("kind", "synthetic")
    # Brief step 21: device and replay are real; a synthetic packet is hybrid.
    data = "real" if kind in {"device", "replay"} else "hybrid" if kind == "synthetic" else "synthetic"
    sensors = ["images", "intrinsics"]
    if packet.get("planes"):
        sensors.append("arkit_planes")
    if packet.get("taps"):
        sensors.append("taps")
    gravity = "arkit" if flow != "photo" and not str(source_frame).startswith("colmap") else "ground_plane_fit"
    return {
        "data": data,
        "site": scene_id,
        "producer": {
            "path": "hss-geometry",
            "version": git_sha(),
            "params": {"scene": params.as_dict(), "scale": dict(scale)},
        },
        "inputs": {
            "packet_id": str(packet.get("packetId", scene_id)),
            "images_given": n_cams,
            "images_used": n_cams,
            "sensors_used": sensors,
        },
        "scale_source": scale_name,
        "scale_reference_ids": ref_ids,
        "gravity_source": gravity,
        "runtime_s": 0.0,
        "hardware": "cpu",
        "cost_usd": 0.0,
        "license": LICENSE,
        "notes": notes,
    }


def _retarget_keyframes(scene: dict, state: dict, scale: dict, pose_sources: list[str]) -> None:
    source = str(scale.get("source", "none"))
    frames = []
    for cam, posed in zip(state["cams_c"], pose_sources, strict=False):
        name = pose_source_name(source, posed)
        frames.append(
            {
                "id": cam.id,
                "pose": load_harness()["frame"].mat4_to_list(cam.pose),
                "intrinsics": [float(v) for v in np.asarray(cam.K).reshape(-1)[:4]],
                "w": int(cam.w),
                "h": int(cam.h),
                "pose_source": name,
            }
        )
    if frames:
        scene["keyframes"] = frames


def attach_assets(scene: dict, out_dir: Path, grid, points: np.ndarray, src: np.ndarray) -> None:
    harness = load_harness()
    sceneio = harness["sceneio"]
    out_dir.mkdir(parents=True, exist_ok=True)
    ply_path = out_dir / "points.ply"
    sceneio.write_ply(
        ply_path,
        np.asarray(points, np.float32),
        {"n_views": np.ones(len(points), np.int32), "src": np.asarray(src, np.uint8)},
    )
    npz_path = out_dir / "coverage.npz"
    grid.save(npz_path)
    scene["assets"] = [
        {
            "id": "points",
            "kind": "point_cloud",
            "path": "points.ply",
            "format": "ply",
            "sha256": sceneio.sha256_file(ply_path),
            "in_scene_frame": True,
            "count": int(len(points)),
            "fields": ["x", "y", "z", "n_views", "src"],
        },
        {
            "id": "coverage",
            "kind": "coverage_grid",
            "path": "coverage.npz",
            "format": "npz",
            "sha256": sceneio.sha256_file(npz_path),
        },
    ]
    scene["coverage"]["grid"] = grid.meta("coverage")
