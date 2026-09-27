"""Regression tests for the harness defects found after the breadth runs.

GT taps rescaled by image width, the 90.2° schema clamp, reversed door spans, a meter on a
short jamb (delivery_area), and a dense cloud that bends or doubles the meter wall.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from server.common.config import default_fixture_source
from server.eval.cli import main
from server.eval.coverage import (
    CoverageQuery,
    MissingOccluderError,
    bands_from_grid,
    build_grid,
    clamp_incidence_deg,
    grade,
)
from server.eval.extract import ExtractParams, extract_scene, normalize_spans, ordered_span
from server.eval.frame import mat4_to_list, project_arkit
from server.eval.geomscore import score_cloud, score_scene
from server.eval.sceneio import write_ply
from server.eval.taps import meter_taps, place_meter_tap
from server.eval.verdictscore import score
from server.eval.visibility import Camera


def _pose(x: float, y: float, z: float) -> np.ndarray:
    pose = np.eye(4)
    pose[:3, 3] = (x, y, z)
    return pose


def _cam(name: str, x: float, y: float, z: float, w: int = 640, h: int = 480, fx: float = 700.0) -> Camera:
    return Camera(name, _pose(x, y, z), np.array([fx, fx, w / 2, h / 2]), w, h)


def _grid(x0, x1, y0, y1, z, step=0.08) -> np.ndarray:
    xs = np.arange(x0, x1 + 1e-9, step)
    ys = np.arange(y0, y1 + 1e-9, step)
    xx, yy = np.meshgrid(xs, ys)
    return np.c_[xx.ravel(), yy.ravel(), np.full(xx.size, z)]


def _ground() -> np.ndarray:
    xs = np.arange(-8, 8.01, 0.35)
    zs = np.arange(-1, 8.01, 0.35)
    xx, zz = np.meshgrid(xs, zs)
    return np.c_[xx.ravel(), np.zeros(xx.size), zz.ravel()]


def _pixel(cam: Camera, xyz: np.ndarray) -> tuple[float, float]:
    u, v, _ = project_arkit(cam.pose, cam.K, np.atleast_2d(xyz))
    return float(u[0]), float(v[0])


def _run(points: np.ndarray, cams: list[Camera], meter: np.ndarray, features: list | None = None) -> dict:
    clicks = [(i, *_pixel(c, meter)) for i, c in enumerate(cams)]
    prov = {"data": "synthetic", "site": "unit", "producer": {"path": "test", "params": {}}}
    scene, state = extract_scene(
        points,
        [np.array([], int) for _ in points],
        cams,
        clicks,
        features or [],
        ExtractParams(seed=1),
        prov,
        "unit",
        "world",
    )
    scene["_report"] = state["report"]
    return scene


def test_clamp_incidence_rejects_90_2():
    assert clamp_incidence_deg(90.2) == 90.0
    assert clamp_incidence_deg(-0.04) == 0.0
    graded = grade(
        {"views_good": np.array([3, 3]), "views_any": np.array([3, 3]), "best_incidence_deg": np.array([90.2, 88.0])},
        {"wall_id": "w1"},
        surface="wall",
        limit=75.0,
    )
    assert graded["worst_best_incidence_deg"] == 90.0
    assert graded["best_incidence_deg"] <= 90.0


def test_reversed_door_span_is_sorted():
    scene = {
        "objects": [{"id": "door", "geometry": {"kind": "wall_rect", "wall_id": "w3", "s_m": [4.2, 1.1]}}],
        "coverage": {"bands": [{"wall_id": "w3", "s_m": [2.0, -1.5]}]},
    }
    normalize_spans(scene)
    assert scene["objects"][0]["geometry"]["s_m"] == [1.1, 4.2]
    assert scene["coverage"]["bands"][0]["s_m"] == [-1.5, 2.0]
    assert ordered_span(3.0, 3.0) == [3.0, 3.0]


def test_extractor_sorts_a_swapped_door():
    wall = _grid(-4, 4, 0.15, 2.8, 0.0)
    cams = [_cam("a", -1.5, 1.6, 6.0), _cam("b", 1.5, 1.6, 6.0)]
    # left/right are swapped on purpose: the +s edge is stored under "left".
    roles = {
        "left": np.array([2.2, 1.2, 0.0]),
        "right": np.array([1.0, 1.2, 0.0]),
        "bottom": np.array([1.6, 0.3, 0.0]),
        "top": np.array([1.6, 2.1, 0.0]),
    }
    views = {i: {role: _pixel(cam, xyz) for role, xyz in roles.items()} for i, cam in enumerate(cams)}
    features = [{"id": "door", "class": "door", "points_px": views}]
    scene = _run(np.vstack([wall, _ground()]), cams, np.array([0.0, 1.5, 0.0]), features)
    door = next(o for o in scene["objects"] if o["id"] == "door")
    s0, s1 = door["geometry"]["s_m"]
    assert s0 <= s1
    assert 0.8 < (s1 - s0) < 1.6


def _ply(path: Path, xyz: list[list[float]]) -> None:
    points = np.asarray(xyz, dtype="<f4")
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(points)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "end_header\n"
    ).encode()
    path.write_bytes(header + points.tobytes())


def _kf_scene(assets: list[dict]) -> dict:
    return {
        "keyframes": [
            {"id": "k", "pose": np.eye(4).tolist(), "intrinsics": [10.0, 10.0, 2.0, 2.0], "w": 4, "h": 4}
        ],
        "assets": assets,
    }


def test_missing_cloud_is_a_file_error_not_a_stack(tmp_path: Path):
    scene = _kf_scene([{"id": "cloud", "kind": "point_cloud", "path": "missing.ply"}])
    with pytest.raises(FileNotFoundError):
        CoverageQuery.from_scene(scene, tmp_path)


def test_missing_occluders_file_is_an_error(tmp_path: Path):
    _ply(tmp_path / "cloud.ply", [[0.0, 0.0, 0.0]])
    scene = _kf_scene(
        [
            {"id": "cloud", "kind": "point_cloud", "path": "cloud.ply"},
            {"id": "occluders", "kind": "point_cloud", "path": "gone.ply"},
        ]
    )
    with pytest.raises(MissingOccluderError):
        CoverageQuery.from_scene(scene, tmp_path)


def test_missing_laser_fails_even_when_occluders_exist(tmp_path: Path):
    """SD-61: an occluders PLY does not stand in for a declared laser or recon cloud."""
    _ply(tmp_path / "occluders.ply", [[0.0, 0.0, 1.0], [1.0, 0.0, 1.0], [0.0, 1.0, 1.0]])
    scene = _kf_scene(
        [
            {"id": "laser", "kind": "point_cloud", "path": "laser.ply"},
            {"id": "occluders", "kind": "point_cloud", "path": "occluders.ply"},
        ]
    )
    with pytest.raises(MissingOccluderError):
        CoverageQuery.from_scene(scene, tmp_path)


def test_allow_missing_occluders_marks_provisional(tmp_path: Path):
    _ply(tmp_path / "cloud.ply", [[0.0, 0.0, 0.0]])
    scene = _kf_scene(
        [
            {"id": "cloud", "kind": "point_cloud", "path": "cloud.ply"},
            {"id": "occluders", "kind": "point_cloud", "path": "gone.ply"},
        ]
    )
    query = CoverageQuery.from_scene(scene, tmp_path, allow_missing_occluders=True)
    assert query.provisional is True
    assert len(query.cams) == 1


def test_crop_tap_fails_the_old_width_rescale():
    """The old extractor multiplied GT pixels by recon_width / gt_width.

    A centred crop keeps the focal length and moves the principal point, so that product is a
    different pixel. The pre-fix ``meter_taps`` returned the product; the fix raises. This
    assertion fails if either function goes back to the width ratio.
    """
    point = np.array([1.0, 1.5, 0.0])
    pose = _pose(0.0, 1.5, 5.0)
    full = Camera("full", pose, np.array([800.0, 800.0, 800.0, 750.0]), 1600, 1500)
    crop = Camera("crop", pose, np.array([800.0, 800.0, 400.0, 375.0]), 800, 750)
    u_full, v_full = _pixel(full, point)
    u_crop, v_crop = _pixel(crop, point)
    u_old = u_full * (crop.w / full.w)
    v_old = v_full * (crop.w / full.w)
    assert abs(u_old - u_crop) > 1 or abs(v_old - v_crop) > 1
    with pytest.raises(ValueError, match="does not match"):
        place_meter_tap(u_full, v_full, full.w, crop.w)
    kept_u, kept_v = place_meter_tap(u_crop, v_crop, crop.w, crop.w)
    assert abs(kept_u - u_crop) < 1e-9 and abs(kept_v - v_crop) < 1e-9
    gt = {
        "frame": {"anchor": {"element_id": "meter"}},
        "objects": [{"id": "meter", "geometry": {"pos_m": point.tolist()}}],
    }
    taps = meter_taps(gt, [crop], {0: crop.w})
    assert len(taps) == 1
    assert abs(taps[0][1] - u_crop) < 1e-6
    assert abs(taps[0][1] - u_old) > 1
    with pytest.raises(ValueError, match="rescale"):
        meter_taps(gt, [full], {0: crop.w})
    source = Path(__file__).resolve().parents[1].joinpath("eth3d", "scene_from_recon.py").read_text()
    assert "taps.append((i, u * r, v * r))" not in source
    assert "roles[role][0] * r" not in source
    assert "opening_clicks" in source
    assert "cameras_in_gt_frame" in source


def test_low_sheet_does_not_steal_the_meter_wall():
    """A ground-strip line extrudes into a sheet that crosses the meter ray first.

    The meter belongs on the tall facade behind it. A sheet whose points stay below
    ``wall_high_m`` is not that wall.
    """
    wall = _grid(-6, 6, 0.15, 2.8, 0.0, step=0.1)
    sheet = _grid(-2, 2, 0.15, 1.2, 1.6, step=0.08)
    cams = [_cam("a", -1.5, 1.6, 6.0), _cam("b", 1.5, 1.6, 6.0)]
    scene = _run(np.vstack([wall, sheet, _ground()]), cams, np.array([0.0, 1.5, 0.0]))
    span = scene["walls"][0]["s_range_m"]
    assert span[1] - span[0] > 8.0


def _arkit_crop(fx: float, fy: float, cx: float, cy: float, w: int, h: int) -> tuple[list[float], int, int, int]:
    """Same centre-crop as packetgen: DSLR pixels become the 1920-wide fixture frame."""
    aspect = w / h
    if 1.2 <= aspect <= 1.55:
        width, height = 1920, 1440
    elif 1.6 <= aspect <= 1.95:
        width, height = 1920, 1080
    else:
        width, height = 1920, int(round(1920 / aspect))
    target = width / height
    if w / h > target:
        crop_w, crop_h = int(round(h * target)), h
    else:
        crop_w, crop_h = w, int(round(w / target))
    x0, y0 = (w - crop_w) // 2, (h - crop_h) // 2
    sx, sy = width / crop_w, height / crop_h
    return [fx * sx, fy * sy, (cx - x0) * sx, (cy - y0) * sy], width, height, x0


def _laser_dir() -> Path | None:
    root = Path(__file__).resolve().parents[3]
    scene = root / "data" / "fixtures" / "scenes" / "eth3d-facade" / "gt-laser"
    candidates = [
        scene,
        root / ".fixtures" / "gt" / "eth3d-facade" / "gt-laser",
        Path(default_fixture_source()) / "eval-harness" / "results" / "eth3d-facade" / "gt-laser",
    ]
    for directory in candidates:
        if (directory / "laser.ply").is_file() and (directory / "occluders.ply").is_file():
            return directory
    return None


def test_gt_poses_on_the_laser_score_about_2_in(tmp_path: Path):
    """GT poses through the fixture crop score about 2.4 in on the facade laser.

    The stock scorer projected DSLR clicks and multiplied by 1920/6201. That is about
    81 px off on these crops, and the same control scored 56.9 in. The patched path
    uses the recon camera's K, width, and height.
    """
    clouds = _laser_dir()
    if clouds is None:
        pytest.skip("facade laser and occluder PLYs are not on this machine")
    gt_src = Path(__file__).resolve().parents[3] / "data" / "fixtures" / "scenes" / "eth3d-facade" / "gt-laser" / "scene.json"
    work = tmp_path / "gt"
    work.mkdir()
    (work / "scene.json").write_bytes(gt_src.read_bytes())
    (work / "laser.ply").symlink_to(clouds / "laser.ply")
    (work / "occluders.ply").symlink_to(clouds / "occluders.ply")
    gt = json.loads(gt_src.read_text())
    rows = []
    crops = []
    for kf in gt["keyframes"]:
        fx, fy, cx, cy = kf["intrinsics"]
        K, width, height, x0 = _arkit_crop(fx, fy, cx, cy, int(kf["w"]), int(kf["h"]))
        crops.append(x0)
        rows.append({"image": f"{kf['id']}.jpg", "pose": kf["pose"], "intrinsics": K, "w": width, "h": height})
    assert min(crops) >= 345 and max(crops) <= 347
    scale = rows[0]["intrinsics"][0] / gt["keyframes"][0]["intrinsics"][0]
    assert abs(scale - 0.3487) < 0.002
    cameras = tmp_path / "cameras.json"
    cameras.write_text(json.dumps(rows))
    report = score_cloud(
        work / "scene.json",
        work / "laser.ply",
        cameras,
        tmp_path / "out",
        True,
        visibility=True,
        producer="gt-poses",
    )
    observed = report["key_distances"]["on_truly_observed_elements"]
    assert observed["n"] >= 20
    # Patched control on this laser: observed p90 2.47 in (n 26). Stock width-ratio mapping is 56.9.
    assert abs(observed["p90_in"] - 2.41) < 0.15
    assert report["scale"]["alignment"]["camera_rms_m"] < 0.01


def test_jamb_past_a_garage_door_still_finds_the_meter_wall():
    wall = _grid(-6, 0, 0.15, 2.8, 0.0)
    jamb = _grid(3.2, 3.8, 0.15, 2.8, 0.0, step=0.1)
    cams = [_cam("a", 3.5, 1.6, 6.0), _cam("b", 2.0, 1.6, 6.5)]
    scene = _run(np.vstack([wall, jamb, _ground()]), cams, np.array([3.5, 1.5, 0.0]))
    assert scene["objects"][0]["class"] == "meter"
    assert scene["chain"]["wall_ids"]


def test_dense_bend_stays_one_meter_wall():
    wall = _grid(-6, 6, 0.15, 2.8, 0.0, step=0.07)
    ang = np.radians(10.0)
    c, s = np.cos(ang), np.sin(ang)
    mask = wall[:, 0] > 0.4
    dx = wall[mask, 0] - 0.4
    dz = wall[mask, 2] - 0.0
    wall[mask, 0] = 0.4 + c * dx - s * dz
    wall[mask, 2] = 0.0 + s * dx + c * dz
    cams = [_cam("a", -2.0, 1.6, 7.0), _cam("b", 2.0, 1.6, 7.0)]
    scene = _run(np.vstack([wall, _ground()]), cams, np.array([-2.0, 1.5, 0.0]))
    assert scene["chain"]["wall_ids"] == ["w1"]
    length = scene["walls"][0]["s_range_m"][1] - scene["walls"][0]["s_range_m"][0]
    assert length > 10.0
    points = np.vstack([wall, _ground()])
    query = CoverageQuery(cams, points, scale=0.25)
    grid = build_grid([(c.pose[:3, 3], points, None) for c in cams], points.min(0) - 0.5, points.max(0) + 0.5, 0.25)
    scene["coverage"]["bands"] = bands_from_grid(grid, scene, query=query, only=("wall",))
    bands = [b for b in scene["coverage"]["bands"] if b.get("wall_id") == "w1" and b.get("band") == "wall"]
    assert bands
    assert min(int(b["views"]) for b in bands) >= 1


def test_window_glass_behind_the_wall_is_not_a_second_wall():
    wall = _grid(-6, 6, 0.15, 2.8, 0.0, step=0.07)
    glass = _grid(-1.0, 1.2, 1.0, 2.2, -0.25, step=0.05)
    cams = [_cam("a", -2.0, 1.6, 7.0), _cam("b", 2.0, 1.6, 7.0)]
    scene = _run(np.vstack([wall, glass, _ground()]), cams, np.array([0.0, 1.5, 0.0]))
    assert scene["chain"]["wall_ids"] == ["w1"]
    assert scene["_report"]["segments_dropped_duplicate"] >= 1
    length = scene["walls"][0]["s_range_m"][1] - scene["walls"][0]["s_range_m"][0]
    assert length > 10.0


def test_run_c1_is_deprecated():
    assert main(["run-c1", "--scene", "unused.json", "--out", "unused-out.json"]) == 2


def test_pass_recall_on_a_labelled_pass():
    labels = {
        "site": "unit",
        "rules_profile": "C",
        "site_decision": "unknown",
        "labels": [
            {
                "id": "spot",
                "scope": "start_range",
                "criterion": "placement",
                "wall_id": "w1",
                "start_s_m": [0.0, 0.0],
                "truth": "pass",
            },
        ],
    }
    verdict = {
        "format": "base-verdict/v0",
        "verdict_id": "t",
        "scene": {"scene_id": "x", "site": "unit"},
        "evaluator": {"path": "test"},
        "site": {"decision": "manual_review", "reasons": []},
        "criteria": [],
        "views_needed": [],
        "candidates": [{"id": "a", "wall_id": "w1", "start_s_m": [-0.2, 0.2], "outcome": "pass"}],
    }
    got = score(verdict, labels)
    assert got["headline"]["labelled_pass_positions"] >= 1
    assert got["headline"]["pass_recall"] == 1.0
    assert got["site"]["pass_recall"] == 1.0


def test_pass_recall_is_none_without_a_labelled_pass():
    labels = {
        "site": "unit",
        "rules_profile": "C",
        "site_decision": "unknown",
        "labels": [
            {
                "id": "spot",
                "scope": "start_range",
                "criterion": "placement",
                "wall_id": "w1",
                "start_s_m": [0.0, 0.0],
                "truth": "not_pass",
            },
        ],
    }
    verdict = {
        "format": "base-verdict/v0",
        "verdict_id": "t",
        "scene": {"scene_id": "x", "site": "unit"},
        "evaluator": {"path": "test"},
        "site": {"decision": "manual_review", "reasons": []},
        "criteria": [],
        "views_needed": [],
        "candidates": [{"id": "a", "wall_id": "w1", "start_s_m": [-0.2, 0.2], "outcome": "unsure"}],
    }
    got = score(verdict, labels)
    assert got["headline"]["pass_recall"] is None
    assert got["headline"]["labelled_pass_positions"] == 0


def test_score_scene_missing_laser_does_not_score_visibility(tmp_path: Path):
    """SD-61: a declared laser or occluder PLY that is not on disk is an error.

    The debug flag marks the report provisional and does not run visibility with
    the blockers omitted.
    """
    source = Path(__file__).resolve().parents[3] / "eval/results/eth3d-facade/gt-laser/scene.json"
    scene = tmp_path / "scene.json"
    scene.write_text(source.read_text())
    with pytest.raises(MissingOccluderError):
        score_scene(scene, scene, tmp_path / "out", visibility=True)
    report = score_scene(scene, scene, tmp_path / "allowed", visibility=True, allow_missing_occluders=True)
    assert report["provisional"] is True
    assert "visibility" not in report


def _camera_json(path: Path, cams: list[Camera]) -> None:
    rows = []
    for cam in cams:
        rows.append(
            {
                "image": f"{cam.id}.jpg",
                "pose": mat4_to_list(cam.pose, nd=8),
                "intrinsics": [float(cam.K[0]), float(cam.K[1]), float(cam.K[2]), float(cam.K[3])],
                "w": cam.w,
                "h": cam.h,
            }
        )
    path.write_text(json.dumps(rows))


def test_score_cloud_cropped_cameras_match_the_full_frame(tmp_path: Path):
    """GT-vs-GT through ``score_cloud``: a centre crop uses the recon ``K``.

    Reverting ``tap_cams`` to the GT intrinsics puts the cropped clicks on the
    width-ratio pixels, and the key-distance score moves.
    """
    wall = _grid(0.2, 5.5, 0.3, 2.6, 0.0, step=0.1)
    full = [
        _cam("c0", 0.8, 1.5, 4.0, w=1600, h=1200, fx=900),
        _cam("c1", 2.4, 1.5, 4.0, w=1600, h=1200, fx=900),
        _cam("c2", 4.0, 1.5, 4.0, w=1600, h=1200, fx=900),
    ]
    crop = [
        Camera(cam.id, cam.pose, np.array([900.0, 900.0, 400.0, 300.0]), 800, 600) for cam in full
    ]
    meter = np.array([0.5, 1.4, 0.0])
    scene = _run(np.vstack([wall, _ground()]), full, meter)
    scene["keyframes"] = [
        {
            "id": cam.id,
            "pose": mat4_to_list(cam.pose, nd=8),
            "intrinsics": [float(x) for x in cam.K],
            "w": cam.w,
            "h": cam.h,
        }
        for cam in full
    ]
    gt_path = tmp_path / "gt" / "scene.json"
    gt_path.parent.mkdir()
    gt_path.write_text(json.dumps(scene))
    cloud = tmp_path / "cloud.ply"
    write_ply(cloud, np.vstack([wall, _ground()]).astype(np.float32))
    full_cams = tmp_path / "full.json"
    crop_cams = tmp_path / "crop.json"
    _camera_json(full_cams, full)
    _camera_json(crop_cams, crop)
    full_score = score_cloud(gt_path, cloud, full_cams, tmp_path / "full", True, visibility=False)
    crop_score = score_cloud(gt_path, cloud, crop_cams, tmp_path / "crop", True, visibility=False)
    full_p90 = full_score["key_distances"]["overall"]["p90_in"]
    crop_p90 = crop_score["key_distances"]["overall"]["p90_in"]
    assert abs(full_p90 - crop_p90) < 0.05
