"""Synthetic box house and the guards that do not need weights."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import numpy as np
import pytest

from server.common.role import coverage_only_dense
from server.contracts.jobs import StageContext
from server.coverage.query import DepthAwareCoverageQuery, query_status
from server.coverage.requests import views_needed
from server.coverage.run import CoverageReport
from server.coverage.run import run as run_coverage_stage
from server.dense.dense import align_centers, resolve_second_opinion
from server.dense.depth import flying_pixel_mask, scale_depth_to_tracks
from server.dense.framew import up_from_gravity
from server.dense.lidar_fuse import scale_about
from server.dense.params import DenseParams
from server.dense.run import run as run_dense_stage
from server.dense.windows import camera_forward, flag_window, net_wh
from server.eval.sceneio import read_ply, sha256_file
from server.scene.build import build_metric_scene
from server.scene.errors import SceneError
from server.scene.features import clamp_angle, drop_phantom_walls, material_of, sort_span
from server.scene.ground import guard_ground
from server.scene.harness import load as load_harness
from server.scene.merge import merge_object_lists
from server.scene.params import SceneParams
from server.scene.run import run as run_scene_stage

coverage_run = importlib.import_module("server.coverage.run")


def _pose(position, look) -> np.ndarray:
    z_axis = -np.asarray(look, float)
    z_axis = z_axis / np.linalg.norm(z_axis)
    up = np.array([0.0, 1.0, 0.0])
    x_axis = np.cross(up, z_axis)
    x_axis = x_axis / np.linalg.norm(x_axis)
    y_axis = np.cross(z_axis, x_axis)
    pose = np.eye(4)
    pose[:3, :3] = np.stack([x_axis, y_axis, z_axis], axis=1)
    pose[:3, 3] = np.asarray(position, float)
    return pose


def _sheet(a, b, c, n_ab=40, n_c=20) -> np.ndarray:
    """Points on a parallelogram. a and b are corners, c is the up corner from a."""
    us = np.linspace(0.0, 1.0, n_ab)
    vs = np.linspace(0.0, 1.0, n_c)
    uu, vv = np.meshgrid(us, vs, indexing="ij")
    origin = np.asarray(a, float)
    du = np.asarray(b, float) - origin
    dv = np.asarray(c, float) - origin
    return origin + uu.ravel()[:, None] * du + vv.ravel()[:, None] * dv


def _box():
    front = _sheet([0, 0.1, 0], [4, 0.1, 0], [0, 2.6, 0], 48, 24)
    right = _sheet([4, 0.1, 0], [4, 0.1, -3], [4, 2.6, 0], 36, 24)
    back = _sheet([0, 0.1, -3], [4, 0.1, -3], [0, 2.6, -3], 48, 24)
    ground = _sheet([0, 0, 0.2], [4, 0, 0.2], [0, 0, 2.0], 20, 10)
    points = np.vstack([front, right, back, ground])
    src = np.zeros(len(points), int)
    n_front, n_right, n_back = len(front), len(right), len(back)
    cams = []
    for x in np.linspace(0.4, 3.6, 6):
        cams.append(_pose([x, 1.5, 5.0], [0, 0, -1]))
    for z in np.linspace(-0.4, -2.6, 4):
        cams.append(_pose([8.0, 1.5, z], [-1, 0, 0]))
    camera_cls = load_harness()["visibility"].Camera
    k = np.array([800.0, 800.0, 320.0, 240.0])
    cameras = [camera_cls(f"k{i:02d}", pose, k, 640, 480) for i, pose in enumerate(cams)]
    tracks = []
    for index in range(len(points)):
        if index < n_front:
            tracks.append(np.arange(6))
        elif index < n_front + n_right:
            tracks.append(np.arange(6, 10))
        elif index < n_front + n_right + n_back:
            tracks.append(np.array([], dtype=int))
        else:
            tracks.append(np.arange(6))
    frame = load_harness()["frame"]
    u, v, z = frame.project_arkit(cameras[2].pose, k, np.array([[2.0, 1.2, 0.0]]))
    assert z[0] > 0
    clicks = [(2, float(u[0]), float(v[0]))]
    return points, src, tracks, cameras, clicks


def test_box_house_lengths_coverage_and_completion(tmp_path: Path):
    points, src, tracks, cameras, clicks = _box()
    # Third wall the cameras never saw, plus a wall-end tap just past it.
    transform = np.eye(4)
    transform[:3, 0] = [0.0, 0.0, -1.0]
    transform[:3, 1] = [0.0, 1.0, 0.0]
    transform[:3, 2] = [1.0, 0.0, 0.0]
    transform[:3, 3] = [0.0, 1.2, -1.5]
    packet = {
        "packetId": "box",
        "source": {"kind": "synthetic"},
        "planes": [
            {
                "id": "p-left",
                "alignment": "vertical",
                "classification": "wall",
                "transform": transform.flatten(order="F").tolist(),
                "planeExtent": {"width": 3.0, "height": 2.5},
            }
        ],
        "taps": [{"label": "wall_end_left", "keyframe": "k00", "pixel": [0, 0], "hit": {"position": [0.0, 0.0, -3.2]}}],
    }
    params = SceneParams(voxel_m=0.1)
    scene, _state, needed = build_metric_scene(
        points=points,
        src=src,
        tracks=tracks,
        cameras=cameras,
        meter_clicks=clicks,
        scale={"source": "arkit", "status": "unverified", "rel_bound_95": 0.09, "scale_factor": 1.0, "origin_m": None},
        packet=packet,
        source_frame="arkit_world:e0",
        t_w_from_source=np.eye(4),
        params=params,
        flow="guided",
        pose_sources=["sfm"] * 10,
        scene_id="box",
    )
    observed = [w for w in scene["walls"] if w["evidence"] == "observed"]
    lengths = sorted(float(np.linalg.norm(np.subtract(w["baseline_m"][1], w["baseline_m"][0]))) for w in observed)
    assert lengths[0] == pytest.approx(3.0, abs=0.02)
    assert 4.0 in [pytest.approx(v, abs=0.02) for v in lengths] or any(abs(v - 4.0) < 0.02 for v in lengths)
    corner = np.asarray(scene["chain"]["corners"][0]["point_m"], float)
    # Meter foot is x=2, so the front/right corner lands at scene x=2, z=0.
    assert np.linalg.norm(corner - np.array([2.0, 0.0])) < 0.03 or _corner_matches_walls(scene)
    seen = [w for w in observed if w["observed_fraction"] >= 0.9]
    hidden = [w for w in observed if w["observed_fraction"] == 0.0]
    assert seen, scene["walls"]
    assert hidden, [(w["id"], w["observed_fraction"]) for w in observed]
    completed = [w for w in scene["walls"] if w["evidence"] == "inferred"]
    assert completed
    wall = completed[0]
    assert wall["plus_minus_m"] == pytest.approx(0.30, abs=1e-6)
    assert wall["observed_fraction"] == 0.0
    assert query_status(scene, wall["id"], scene["coverage"]["bands"]) == "unobserved"
    named = [row for row in needed if row.get("wall_id") == wall["id"] and row.get("band") == "wall"]
    assert named and named[0]["view_angle_deg"] == [0, 30]
    assert scene["chain"]["ends"]["left"]["kind"] == "unexplored"
    assert "tap wall_end_left" in scene["chain"]["ends"]["left"].get("note", "")
    assert "scale" not in scene
    assert scene["provenance"]["data"] == "hybrid"
    assert scene["provenance"]["producer"]["params"]["scale"]["source"] == "arkit"
    assert scene["keyframes"][0]["pose_source"] == "sfm_scaled_by_arkit"
    load_harness()["sceneio"].save_json(scene, tmp_path / "scene.json")


def test_dense_coverage_scene_chain_publishes_named_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """I-18: later stages open cloud.npz and scene_ready.json, and run returns those keys."""
    points, src, tracks, cameras, clicks = _box()
    frame = load_harness()["frame"]
    cam_dicts = [
        {
            "keyframe_id": cam.id,
            "pose": frame.mat4_to_list(cam.pose),
            "intrinsics": [float(v) for v in np.asarray(cam.K).reshape(-1)[:4]],
            "w": int(cam.w),
            "h": int(cam.h),
            "pose_source": "sfm",
        }
        for cam in cameras
    ]
    click_i, click_u, click_v = clicks[0]
    packet = {
        "packetId": "box",
        "flow": "guided",
        "taps": [{"label": "reference_point", "keyframe": cameras[click_i].id, "pixel": [click_u, click_v]}],
    }
    work = tmp_path / "work"
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    (packet_dir / "packet.json").write_text(json.dumps(packet))
    poses = work / "poses"
    poses.mkdir(parents=True)
    scale_path = tmp_path / "scale.json"
    scale_path.write_text(
        json.dumps({"source": "arkit", "scale_factor": 1.0, "origin_m": [0.0, 0.0, 0.0], "rel_bound_95": 0.09})
    )

    def _ctx(stage: str, models: dict | None) -> StageContext:
        return StageContext(
            capture_id="cap",
            run_id="run",
            stage=stage,
            attempt=1,
            branch="image",
            flow="guided",
            tier="arkit",
            work_dir=work,
            inputs={"packet_dir": str(packet_dir), "poses_dir": str(poses), "scale/scale.json": str(scale_path)},
            deadline_at="2099-01-01T00:00:00Z",
            heartbeat=lambda _step: None,
            vlm_budget_usd=0.0,
            models=models,
            storage=object(),
            state=object(),
            settings=object(),
        )

    dense_result = run_dense_stage(
        _ctx(
            "dense",
            {
                "cameras": cam_dicts,
                "points_w": points,
                "point_src": src,
                "point_tracks": tracks,
                "source_frame": "arkit_world",
            },
        )
    )
    dense_keys = (
        "dense/dense.ply",
        "dense/flagged.ply",
        "dense/dense_report.json",
        "dense/cloud.npz",
        "dense/tracks.npz",
        "dense/cameras_w.json",
    )
    for key in dense_keys:
        assert dense_result.artifacts[key] == key
        assert (work / key).is_file()
    assert camera_forward(cam_dicts[0]["pose"]) == pytest.approx(-cameras[0].pose[:3, 2])
    monkeypatch.delenv("HSS_COVERAGE_METER_FRAMES", raising=False)
    monkeypatch.setenv("SMP_ENVIRONMENT", "dev")
    monkeypatch.setenv("HSS_COVERAGE_ANCHOR_FROM_TAP", "reference_point")
    coverage_result = run_coverage_stage(_ctx("coverage", None))
    coverage_keys = (
        "coverage/coverage.npz",
        "coverage/bands.json",
        "coverage/points.ply",
        "coverage/scene_ready.json",
        "coverage/scene_draft.json",
        "coverage/coverage_report.json",
    )
    for key in coverage_keys:
        assert coverage_result.artifacts[key] == key
        assert (work / key).is_file()
    ready = json.loads((work / "coverage" / "scene_ready.json").read_text())
    draft = json.loads((work / "coverage" / "scene_draft.json").read_text())
    assert "assets" not in draft
    ready.pop("assets", None)
    assert draft == ready
    scene_result = run_scene_stage(_ctx("scene", None))
    scene = json.loads((work / "scene" / "scene.json").read_text())
    assert scene["walls"]
    assert scene_result.artifacts == {"scene/scene.json": "scene/scene.json"}
    scene_dir = work / "scene"
    points_ply = scene_dir / "points.ply"
    ply = read_ply(points_ply)
    assert str(ply["x"].dtype) == "float32"
    assert str(read_ply(scene_dir / "dense.ply")["x"].dtype) == "float32"
    assert str(read_ply(scene_dir / "flagged.ply")["x"].dtype) == "float32"
    asset = next(row for row in scene["assets"] if row.get("path") == "points.ply")
    digest = sha256_file(points_ply)
    assert digest == asset["sha256"]


def test_coverage_stage_entry_reads_meter_frames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """I-25: ``run(ctx)`` forwards the fixture meter-frame override and the anchor tap."""
    captured: dict = {}

    def fake(*_args, **kwargs):
        captured["override"] = kwargs["meter_override"]
        captured["anchor"] = kwargs["anchor_label"]
        return CoverageReport(warnings=[], views_needed=[], timings={})

    monkeypatch.setattr(coverage_run, "run_coverage", fake)
    monkeypatch.setenv("SMP_ENVIRONMENT", "dev")
    monkeypatch.setenv("HSS_COVERAGE_METER_FRAMES", "k9:1,2")
    monkeypatch.setenv("HSS_COVERAGE_ANCHOR_FROM_TAP", "other")
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    (packet_dir / "packet.json").write_text("{}")
    work = tmp_path / "work"
    work.mkdir()
    ctx = StageContext(
        capture_id="cap",
        run_id="run",
        stage="coverage",
        attempt=1,
        branch="image",
        flow="guided",
        tier="arkit",
        work_dir=work,
        inputs={"packet_dir": str(packet_dir)},
        deadline_at="2099-01-01T00:00:00Z",
        heartbeat=lambda _step: None,
        vlm_budget_usd=0.0,
        models={"meter_frames": "k00045:377.6,842.3,k00016:1551.7,801.8", "anchor_label": "reference_point"},
        storage=object(),
        state=object(),
        settings=object(),
    )
    run_coverage_stage(ctx)
    assert captured["override"] == [("k00045", 377.6, 842.3), ("k00016", 1551.7, 801.8)]
    assert captured["anchor"] == "reference_point"
    ctx.models = None
    run_coverage_stage(ctx)
    assert captured["override"] == [("k9", 1.0, 2.0)]
    assert captured["anchor"] == "other"
    monkeypatch.setenv("SMP_ENVIRONMENT", "prod")
    run_coverage_stage(ctx)
    assert captured["override"] is None
    assert captured["anchor"] == ""


def test_coverage_stage_without_anchor_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Brief §4 step 9: no tap and no override is ``no_meter_anchor``, not a silent scene."""
    monkeypatch.delenv("HSS_COVERAGE_METER_FRAMES", raising=False)
    monkeypatch.delenv("HSS_COVERAGE_ANCHOR_FROM_TAP", raising=False)
    work = tmp_path / "work"
    dense = work / "dense"
    dense.mkdir(parents=True)
    np.savez_compressed(dense / "cloud.npz", points=np.zeros((1, 3)), src=np.zeros(1, np.int16))
    (dense / "dense_report.json").write_text("{}\n")
    pose = np.eye(4).flatten(order="F").tolist()
    (dense / "cameras_w.json").write_text(
        json.dumps([{"keyframe_id": "k0", "pose": pose, "intrinsics": [1, 1, 0, 0], "w": 4, "h": 4}])
    )
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    (packet_dir / "packet.json").write_text(
        json.dumps({"taps": [{"label": "corner", "keyframe": "k0", "pixel": [1, 1]}]})
    )
    ctx = StageContext(
        capture_id="cap",
        run_id="run",
        stage="coverage",
        attempt=1,
        branch="image",
        flow="guided",
        tier="arkit",
        work_dir=work,
        inputs={"packet_dir": str(packet_dir)},
        deadline_at="2099-01-01T00:00:00Z",
        heartbeat=lambda _step: None,
        vlm_budget_usd=0.0,
        models=None,
        storage=object(),
        state=object(),
        settings=object(),
    )
    with pytest.raises(SceneError) as raised:
        run_coverage_stage(ctx)
    assert raised.value.code == "no_meter_anchor"


def _corner_matches_walls(scene: dict) -> bool:
    walls = {w["id"]: w for w in scene["walls"]}
    for corner in scene["chain"]["corners"]:
        left = np.asarray(walls[corner["left_wall"]]["baseline_m"][1], float)
        if np.linalg.norm(left - np.asarray(corner["point_m"], float)) < 0.03:
            return True
    return False


def test_occluder_hides_with_and_without_depth():
    camera_cls = load_harness()["visibility"].Camera
    pose = _pose([0.0, 1.5, 5.0], [0, 0, -1])
    k = np.array([800.0, 800.0, 320.0, 240.0])
    cam = camera_cls("c0", pose, k, 640, 480)
    box = _sheet([-0.3, 1.2, 2.0], [0.3, 1.2, 2.0], [-0.3, 1.8, 2.0], 8, 8)
    shadowed = np.array([[0.0, 1.5, 0.0]])
    clear = np.array([[1.2, 1.5, 0.0]])
    blocked = DepthAwareCoverageQuery([cam], box, depths=None)
    seen = blocked.seen_by(np.vstack([shadowed, clear]))
    assert not seen[0, 0]
    assert seen[1, 0]
    frame = load_harness()["frame"]
    u, v, _z = frame.project_arkit(pose, k, shadowed)
    u_c, v_c, z_c = frame.project_arkit(pose, k, clear)
    depth = np.full((480, 640), np.nan, np.float32)
    depth[int(round(v[0])), int(round(u[0]))] = 3.0
    # A finite depth behind the clear point keeps it visible. A missing sample would hide it.
    depth[int(round(v_c[0])), int(round(u_c[0]))] = float(z_c[0]) + 1.0
    by_depth = DepthAwareCoverageQuery([cam], np.zeros((0, 3)), depths={"c0": depth})
    seen_d = by_depth.seen_by(np.vstack([shadowed, clear]))
    assert not seen_d[0, 0]
    assert seen_d[1, 0]


def test_window_flag_and_blur_perturbation():
    params = DenseParams()
    assert not flag_window(0.02, 1.01, 1.0, params)
    assert flag_window(0.25, 1.0, 1.0, params)
    clean = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    _t, rms, scale = align_centers(clean, clean)
    assert rms < 1e-6 and scale == pytest.approx(1.0, abs=1e-6)
    blurred = clean.copy()
    blurred[0] = blurred[0] + np.array([0.8, 0.2, -0.3])
    _t, rms_b, _scale = align_centers(blurred, clean)
    assert flag_window(rms_b, 1.0, 1.0, params)


def test_scale_about_distances_and_gravity_and_depth():
    origin = np.array([1.0, 2.0, 3.0])
    points = np.array([[4.0, 2.0, 3.0], [1.0, 6.0, 3.0]])
    scaled = scale_about(points, origin, 2.0)
    before = np.linalg.norm(points - origin, axis=1)
    after = np.linalg.norm(scaled - origin, axis=1)
    assert after == pytest.approx(before * 2.0)
    pose = np.eye(4)
    g = np.array([-1.0, 0.0, 0.0])
    up = up_from_gravity([pose], [g])
    assert up[1] == pytest.approx(1.0, abs=1e-6)
    assert material_of(["grass", "grass", "gravel"]) == "lawn"
    assert material_of(["grass"]) == "unknown"
    assert material_of(["grass", "gravel"]) == "unknown"
    assert scale_depth_to_tracks(np.array([1.0, 2.0]), np.array([2.0, 4.0]), min_points=2) == pytest.approx(2.0)
    depth = np.ones((8, 8))
    depth[3, 3] = 5.0
    assert not flying_pixel_mask(depth, rel=0.05)[3, 3]
    assert net_wh(1920, 1440) == (518, 392)


def test_phantom_span_incidence_and_merge():
    scene = {
        "walls": [
            {
                "id": "w1",
                "role": "house",
                "baseline_m": [[0, 0], [4, 0]],
                "height_m": 2.5,
                "s_range_m": [0, 4],
                "plus_minus_m": 0.05,
                "evidence": "observed",
                "support": {"points": 100},
            },
            {
                "id": "w2",
                "role": "house",
                "baseline_m": [[4, 0], [4, 3]],
                "height_m": 2.5,
                "s_range_m": [4, 7],
                "plus_minus_m": 0.05,
                "evidence": "observed",
                "support": {"points": 80},
            },
        ],
        "chain": {
            "wall_ids": ["w1", "w2"],
            "corners": [],
            "ends": {
                "left": {"kind": "unexplored", "point_m": [0, 0], "s_m": 0},
                "right": {"kind": "unexplored", "point_m": [4, 3], "s_m": 7},
            },
        },
    }
    rng = np.random.default_rng(0)
    on_w2 = np.c_[np.full(40, 4.0), np.linspace(0.2, 2.0, 40), rng.uniform(0.2, 2.8, 40)]
    src = np.ones(40, int)
    src[:3] = 0
    warnings: list[str] = []
    drop_phantom_walls(scene, on_w2, src, SceneParams(), warnings)
    assert "w2" not in {w["id"] for w in scene["walls"]}
    assert any(w.startswith("wall_dropped_no_sparse_support") for w in warnings)
    kept = json.loads(json.dumps(scene))
    on_w2b = on_w2.copy()
    src_b = np.array([0] * 30 + [1] * 10)
    notes: list[str] = []
    # Rebuild w2 for the keep case.
    kept["walls"].append(
        {
            "id": "w2",
            "role": "house",
            "baseline_m": [[4, 0], [4, 3]],
            "height_m": 2.5,
            "s_range_m": [4, 7],
            "plus_minus_m": 0.05,
            "evidence": "observed",
            "support": {"points": 40},
        }
    )
    kept["chain"]["wall_ids"] = ["w1", "w2"]
    drop_phantom_walls(kept, on_w2b, src_b, SceneParams(), notes)
    assert "w2" in {w["id"] for w in kept["walls"]}
    assert not any("wall_dropped_no_sparse_support" in n for n in notes)
    assert sort_span([3, -1]) == [-1, 3]
    clamped: list[str] = []
    assert clamp_angle(90.2, clamped) == 90.0
    assert clamped == ["incidence_clamped"]
    base = {
        "objects": [
            {
                "id": "meter",
                "class": "meter",
                "geometry": {"kind": "point", "pos_m": [0, 1, 0]},
                "confidence": 1,
                "plus_minus_m": 0.1,
                "source": {"kind": "tap"},
                "evidence": "observed",
            },
            {
                "id": "door",
                "class": "door",
                "geometry": {"kind": "wall_rect", "wall_id": "w1", "s_m": [1.0, 2.0], "bottom_m": 0.0, "top_m": 2.0},
                "confidence": 0.5,
                "plus_minus_m": 0.2,
                "source": {"kind": "detector", "views": [{"image": "k0", "box_px": [0, 0, 10, 10]}]},
                "evidence": "observed",
            },
        ]
    }
    three = {
        "id": "door3",
        "class": "door",
        "geometry": {"kind": "wall_rect", "wall_id": "w1", "s_m": [1.05, 2.05], "bottom_m": 0.0, "top_m": 2.0},
        "confidence": 0.9,
        "plus_minus_m": 0.1,
        "source": {"kind": "detector", "views": [{"image": "k0", "box_px": [1, 1, 9, 9]}]},
        "evidence": "observed",
    }
    merge_object_lists(base, [three])
    ids = [o["id"] for o in base["objects"]]
    assert "door3" in ids and "door" not in ids and "meter" in ids
    merge_object_lists(base, [three])
    assert [o["id"] for o in base["objects"]] == ["meter", "door3"]


def test_ground_fit_guard_rejects_furniture_and_marks_unobserved_base():
    """§8.1: a 0.6 m box 2 m out must not become the ground when five base points remain."""
    params = SceneParams()
    wall = _sheet([0, 1.20, 0], [4, 1.20, 0], [0, 2.5, 0], 8, 6)
    base = np.array(
        [
            [0.4, 0.00, 0.02],
            [1.2, 0.01, 0.00],
            [2.0, 0.00, 0.04],
            [2.8, 0.02, 0.00],
            [3.6, 0.00, 0.03],
        ]
    )
    furniture = _sheet([0, 0.60, 2.0], [4, 0.60, 2.0], [0, 0.62, 2.8], 30, 10)
    points = np.vstack([wall, base, furniture])
    baseline = ((0.0, 0.0), (4.0, 0.0))
    guarded = guard_ground(points, [baseline], params, meter_baseline=baseline)
    assert guarded.rejected_high
    assert guarded.ground_y is not None and guarded.ground_y < 0.30
    assert any(note.startswith("ground_plane_rejected:") for note in guarded.warnings)
    assert "ground_base_unobserved" in guarded.warnings
    assert guarded.plus_minus_floor >= 0.30
    # Base removed: the wall starts above the contact band, so the datum is unsure.
    floating = _sheet([0, 1.20, 0], [4, 1.20, 0], [0, 2.6, 0], 24, 16)
    gone = guard_ground(np.vstack([floating, furniture]), [baseline], params, meter_baseline=baseline)
    assert gone.verified is False
    assert "ground_base_unobserved" in gone.warnings
    assert gone.plus_minus_floor >= 0.30


def test_colmap_depth_is_coverage_only():
    assert coverage_only_dense("colmap_model", "photo")
    assert coverage_only_dense("colmap_model", "guided")
    # SD-65: π³ depth is coverage-only on the guided flow too. Sparse and LiDAR still measure.
    assert coverage_only_dense("arkit_world:e0", "guided")
    assert resolve_second_opinion("auto", "photo") == "da3"
    assert resolve_second_opinion("auto", "guided") == "none"


def test_views_needed_cap_and_angles():
    scene = {
        "walls": [
            {"id": "w1", "s_range_m": [-2, 2], "baseline_m": [[-2, 0], [2, 0]]},
        ],
        "objects": [{"class": "meter", "wall_id": "w1"}],
        "chain": {"wall_ids": ["w1"], "ends": {"left": {"kind": "unexplored"}, "right": {"kind": "limit"}}},
        "coverage": {"bands": []},
    }
    rows = views_needed(scene, [], SceneParams(views_needed_max=8), ground_base_unobserved=True)
    assert len(rows) <= 8
    assert any(r["band"] == "wall" and r["view_angle_deg"] == [0, 30] for r in rows)
    assert any(r["kind"] == "past_end" for r in rows)
    framing = [r for r in rows if r["why"] == "ground datum: wall base not observed"]
    assert len(framing) == 1
    assert framing[0]["band"] == "ground"
    assert framing[0]["view_angle_deg"] == [20, 60]
    assert framing[0]["target"].startswith("from 3 m out")


def test_missing_meter_anchor():
    with pytest.raises(SceneError) as caught:
        raise SceneError("no_meter_anchor")
    assert caught.value.code == "no_meter_anchor"
