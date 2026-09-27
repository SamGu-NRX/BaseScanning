"""Validator fixes: deadline parsing, LiDAR fusion, tracks, merge, ground re-anchor."""

from __future__ import annotations

import inspect
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from server.common.errors import SceneError
from server.contracts.jobs import StageContext
from server.dense.lidar_fuse import tsdf_points, unproject
from server.dense.models import DA3_REPO, MOGE_WEIGHTS, PI3_REPO, load_da3, load_moge, load_pi3
from server.dense.params import DenseParams, dense_params_from_env
from server.dense.run import deadline_epoch, run, run_dense
from server.scene.build import _tracks_in_scene
from server.scene.ground import guard_ground, reanchor_ground
from server.scene.harness import load as load_harness
from server.scene.merge import merge_object_lists
from server.scene.params import SceneParams
from server.scene.run import build_scene


def test_deadline_epoch_parses_iso_z():
    parsed = deadline_epoch("2026-09-27T02:00:00Z")
    assert parsed == pytest.approx(deadline_epoch("2026-09-27T02:00:00+00:00"))
    assert deadline_epoch(1_700_000_000) == pytest.approx(1_700_000_000.0)
    assert deadline_epoch(None) is None


def test_dense_run_accepts_a_real_stage_context(tmp_path: Path):
    packet = tmp_path / "packet"
    packet.mkdir()
    (packet / "packet.json").write_text("{}")
    poses = tmp_path / "poses"
    poses.mkdir()
    pose = np.eye(4)
    cameras = [{"keyframe_id": "k0", "pose": pose.tolist(), "intrinsics": [8, 8, 2, 2], "w": 4, "h": 4}]
    ctx = StageContext(
        capture_id="cap",
        run_id="run",
        stage="dense",
        attempt=1,
        branch="image",
        flow="guided",
        tier="arkit",
        work_dir=tmp_path / "work",
        inputs={"packet_dir": str(packet), "poses_dir": str(poses)},
        deadline_at="2099-01-01T00:00:00Z",
        heartbeat=lambda _step: None,
        vlm_budget_usd=0.0,
        models={"cameras": cameras, "points_w": np.zeros((2, 3)), "point_src": np.zeros(2, int)},
        storage=object(),
        state=object(),
        settings=object(),
    )
    result = run(ctx)
    assert result.metrics["points"] == 2.0
    published = (
        "dense/dense.ply",
        "dense/flagged.ply",
        "dense/dense_report.json",
        "dense/cloud.npz",
        "dense/tracks.npz",
        "dense/cameras_w.json",
    )
    for key in published:
        assert result.artifacts[key] == key
        assert (tmp_path / "work" / key).is_file()


def test_dense_run_rejects_a_past_deadline(tmp_path: Path):
    packet = tmp_path / "packet"
    packet.mkdir()
    poses = tmp_path / "poses"
    poses.mkdir()
    ctx = StageContext(
        capture_id="cap",
        run_id="run",
        stage="dense",
        attempt=1,
        branch="image",
        flow="guided",
        tier="arkit",
        work_dir=tmp_path / "work",
        inputs={"packet_dir": str(packet), "poses_dir": str(poses)},
        deadline_at="2000-01-01T00:00:00Z",
        heartbeat=lambda _step: None,
        vlm_budget_usd=0.0,
        models={
            "cameras": [{"keyframe_id": "k0", "pose": np.eye(4).tolist(), "intrinsics": [1, 1, 0, 0], "w": 2, "h": 2}]
        },
        storage=object(),
        state=object(),
        settings=object(),
    )
    with pytest.raises(SceneError) as caught:
        run(ctx)
    assert caught.value.code == "timeout"


def test_unproject_looks_along_negative_z():
    depth = np.full((4, 4), 2.0)
    k = np.array([4.0, 4.0, 2.0, 2.0])
    cam = unproject(depth, k, 4, 4)
    assert np.all(cam[:, 2] < 0)
    pose = np.eye(4)
    world = cam @ pose[:3, :3].T + pose[:3, 3]
    assert world[:, 2] == pytest.approx(-2.0)


def test_tsdf_uses_voxel_block_grid_api():
    source = inspect.getsource(tsdf_points)
    assert "FrustumBlockRange" not in source
    assert "compute_unique_block_coordinates" in source
    assert "to_legacy" in source


def test_tsdf_one_view_plane_is_kept():
    pytest.importorskip("open3d")
    plane = np.full((64, 64), 2.0, np.float32)
    verts = tsdf_points(
        [plane],
        [np.eye(4)],
        [np.array([50.0, 50.0, 32.0, 32.0])],
        [(64, 64)],
        DenseParams(),
    )
    assert len(verts) > 0


def test_tsdf_skips_a_frame_past_lidar_max():
    pytest.importorskip("open3d")
    far = np.full((32, 32), 100.0, np.float32)
    plane = np.full((32, 32), 2.0, np.float32)
    k = np.array([40.0, 40.0, 16.0, 16.0])
    verts = tsdf_points([far, plane], [np.eye(4), np.eye(4)], [k, k], [(32, 32), (32, 32)], DenseParams())
    assert len(verts) > 0


def test_lidar_points_fuse_without_window_predictions(tmp_path: Path):
    poses = tmp_path / "poses"
    poses.mkdir()
    pose = np.eye(4)
    depth = np.full((8, 8), 2.0)
    report = run_dense(
        {},
        poses,
        {"source": "lidar", "scale_factor": 1.0, "origin_m": [0, 0, 0], "rel_bound_95": 0.02},
        tmp_path / "dense",
        params=DenseParams(geometry="lidar", lidar_method="points"),
        models={
            "cameras": [{"keyframe_id": "k0", "pose": pose.tolist(), "intrinsics": [8, 8, 4, 4], "w": 8, "h": 8}],
            "lidar_depths": [{"depth": depth, "pose": pose.tolist(), "intrinsics": [8, 8, 4, 4], "w": 8, "h": 8}],
            "flow": "guided",
        },
    )
    cloud = np.load(tmp_path / "dense" / "cloud.npz")
    assert int((cloud["src"] == 2).sum()) > 0
    assert "lidar_fused" in report.warnings
    assert report.second_opinion == "none"


def test_image_branch_loads_moge_and_da3_not_injected_points(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """SD-65: a photo-flow run calls the real loader hooks. Tests stub the weights."""
    calls: list[str] = []

    def fake_pi3(images, cameras, params, device="cuda"):
        calls.append("pi3")
        centers = np.stack([np.asarray(c["pose"], float)[:3, 3] for c in cameras[:3]])
        return [{"frames": [0, 1, 2], "pred_centers": centers, "points": np.zeros((6, 3))}]

    def fake_moge(images, cameras, params, device="cuda"):
        calls.append("moge")
        key = str(cameras[0]["keyframe_id"])
        return {key: {"depth": np.ones((2, 2), np.float32), "scale": 1.0, "source": "moge2"}}

    def fake_da3(images, params, device="cuda"):
        calls.append("da3")
        return {"frames": len(images), "finite": 4}

    # export_run replaces the server.dense.run module attribute with the stage function.
    dense_run = sys.modules["server.dense.run"]
    monkeypatch.setattr(dense_run, "infer_pi3_predictions", fake_pi3)
    monkeypatch.setattr(dense_run, "infer_moge_depths", fake_moge)
    monkeypatch.setattr(dense_run, "infer_da3_second_opinion", fake_da3)
    poses = tmp_path / "poses"
    poses.mkdir()
    cameras = []
    images = []
    for index in range(3):
        pose = np.eye(4)
        pose[0, 3] = index * 0.3
        cameras.append({"keyframe_id": f"k{index}", "pose": pose.tolist(), "intrinsics": [8, 8, 4, 4], "w": 8, "h": 8})
        images.append(np.zeros((8, 8, 3), np.uint8))
    photo = run_dense(
        {"packetId": "photo", "taps": [{"label": "meter", "keyframe": "k0", "pixel": [4, 4]}]},
        poses,
        {"source": "none", "scale_factor": 1.0, "origin_m": [0, 0, 0], "rel_bound_95": 0.05},
        tmp_path / "photo",
        params=DenseParams(geometry="image", second_opinion="da3"),
        models={"cameras": cameras, "images": images, "flow": "photo", "device": "cpu"},
    )
    assert calls == ["pi3", "moge", "da3"]
    assert photo.second_opinion == "da3"
    assert "moge_depth" in photo.warnings
    assert "da3_second_opinion" in photo.warnings
    assert photo.geometry_role == "coverage_only"
    assert (tmp_path / "photo" / "depth" / "k0.npz").is_file()

    calls.clear()
    guided = run_dense(
        {},
        poses,
        {"source": "arkit", "scale_factor": 1.0, "origin_m": [0, 0, 0], "rel_bound_95": 0.05},
        tmp_path / "guided",
        params=DenseParams(geometry="image", second_opinion="none"),
        models={"cameras": cameras, "images": images, "flow": "guided", "device": "cpu"},
    )
    assert calls == ["pi3", "moge"]
    assert guided.second_opinion == "none"
    assert "da3_second_opinion" not in guided.warnings


def test_flagged_points_take_the_frame_w_scale(tmp_path: Path):
    poses = tmp_path / "poses"
    poses.mkdir()
    pose = np.eye(4)
    run_dense(
        {},
        poses,
        {"source": "arkit", "scale_factor": 2.0, "origin_m": [0, 0, 0], "rel_bound_95": 0.02},
        tmp_path / "dense",
        params=DenseParams(),
        models={
            "cameras": [{"keyframe_id": "k0", "pose": pose.tolist(), "intrinsics": [1, 1, 0, 0], "w": 4, "h": 4}],
            "points_w": np.array([[1.0, 0.0, 0.0]]),
            "point_src": np.array([0]),
            "flagged_points": np.array([[1.0, 0.0, 0.0]]),
        },
    )
    ply = load_harness()["sceneio"].read_ply(tmp_path / "dense" / "flagged.ply")
    assert float(ply["x"][0]) == pytest.approx(2.0)


def test_merge_keeps_an_unrelated_detector_and_an_empty_list():
    door = {
        "id": "door",
        "class": "door",
        "geometry": {"kind": "wall_rect", "wall_id": "w1", "s_m": [1.0, 2.0], "bottom_m": 0.0, "top_m": 2.0},
        "source": {"kind": "detector"},
    }
    window = {
        "id": "window",
        "class": "window",
        "geometry": {"kind": "wall_rect", "wall_id": "w1", "s_m": [3.0, 3.4], "bottom_m": 0.8, "top_m": 1.6},
        "source": {"kind": "detector"},
    }
    scene = {"objects": [door, window]}
    merge_object_lists(scene, [])
    assert [obj["id"] for obj in scene["objects"]] == ["door", "window"]
    replacement = dict(door)
    replacement["id"] = "door3"
    merge_object_lists(scene, [replacement])
    ids = [obj["id"] for obj in scene["objects"]]
    assert ids == ["window", "door3"]


def test_scene_tracks_never_invent_camera_zero():
    measure = np.array([[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]])
    tracks = [np.array([3], np.int32), np.array([4], np.int32)]
    # A different count forces a rematch. A point with no neighbour claims no camera.
    scene_pts = np.array([[0.0, 0.0, 0.0], [9.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
    matched = _tracks_in_scene(measure, tracks, scene_pts, np.eye(4))
    assert matched[0].tolist() == [3]
    assert matched[1].tolist() == []
    assert matched[2].tolist() == []
    assert all(0 not in row.tolist() for row in matched)


def test_ground_guard_does_not_verify_a_wall_that_reaches_the_slab():
    """A wall down to 0.1 m plus a 0.6 m slab must not be a verified datum 0.21 m high."""
    params = SceneParams()
    wall = []
    for y in np.linspace(0.10, 2.5, 80):
        for x in np.linspace(0.0, 4.0, 40):
            wall.append([x, y, 0.0])
    wall = np.asarray(wall)
    base = np.array(
        [
            [0.4, 0.00, 0.02],
            [1.2, 0.01, 0.00],
            [2.0, 0.00, 0.04],
            [2.8, 0.02, 0.00],
            [3.6, 0.00, 0.03],
        ]
    )
    slab = []
    for x in np.linspace(0.0, 4.0, 30):
        for z in np.linspace(2.0, 2.8, 10):
            slab.append([x, 0.60, z])
    points = np.vstack([wall, base, np.asarray(slab)])
    baseline = ((0.0, 0.0), (4.0, 0.0))
    guarded = guard_ground(points, [baseline], params, meter_baseline=baseline)
    assert guarded.rejected_high
    assert guarded.ground_y is not None and guarded.ground_y < 0.05
    assert guarded.verified is False
    assert "ground_base_unobserved" in guarded.warnings
    assert guarded.plus_minus_floor >= 0.30


def test_reanchor_puts_the_rejected_ground_at_zero():
    scene = {
        "ground": {"surface": {"kind": "plane", "normal": [0.2, 0.9, 0.0], "offset_m": 0.6}, "plus_minus_m": 0.03},
        "walls": [{"id": "w1", "height_m": 2.0}],
        "objects": [{"id": "meter", "class": "meter", "geometry": {"kind": "point", "pos_m": [0.0, 0.6, 0.0]}}],
        "frame": {},
    }
    state = {"points_c": np.array([[0.0, -0.6, 0.0]]), "cams_c": [], "T": np.eye(4)}
    reanchor_ground(scene, state, -0.6)
    assert scene["ground"]["surface"]["offset_m"] == 0.0
    assert state["points_c"][0, 1] == pytest.approx(0.0)
    assert scene["objects"][0]["geometry"]["pos_m"][1] == pytest.approx(1.2)
    assert scene["walls"][0]["height_m"] == pytest.approx(2.6)


def test_missing_scene_ready_names_the_file_it_reads(tmp_path: Path):
    coverage = tmp_path / "coverage"
    coverage.mkdir()
    with pytest.raises(SceneError) as caught:
        build_scene(
            {}, coverage, tmp_path / "dense", tmp_path / "poses", {}, None, tmp_path / "out", params=SceneParams()
        )
    assert caught.value.code == "input_missing:scene_ready"
    assert "scene_ready.json" in str(caught.value)


def test_build_scene_copies_the_given_pose_and_dense_dirs(tmp_path: Path, monkeypatch):
    coverage = tmp_path / "coverage"
    coverage.mkdir()
    dense = tmp_path / "elsewhere" / "dense"
    dense.mkdir(parents=True)
    poses = tmp_path / "elsewhere" / "poses"
    poses.mkdir()
    (dense / "dense.ply").write_bytes(b"dense-bytes")
    (dense / "flagged.ply").write_bytes(b"flagged-bytes")
    (poses / "sparse.ply").write_bytes(b"sparse-bytes")
    scene = {
        "provenance": {"notes": [], "producer": {"params": {"scale": {"source": "arkit"}}}},
        "coverage": {"method": "rays"},
        "walls": [],
    }
    (coverage / "scene_ready.json").write_text("{}")
    module = sys.modules["server.scene.run"]
    monkeypatch.setattr(module, "_load_grid", lambda *_args: None)
    monkeypatch.setattr(module, "_load_points", lambda *_args: np.zeros((1, 3)))
    monkeypatch.setattr(module, "_load_src", lambda *_args, **_kwargs: np.zeros(1, int))
    monkeypatch.setattr(module, "attach_assets", lambda *_args: None)
    monkeypatch.setattr(module, "check_scene", lambda *_args: None)
    monkeypatch.setattr(module, "render_topdown", lambda *_args: None)
    real_loads = module.json.loads

    def _loads(text, *args, **kwargs):
        if text == "{}":
            return scene
        return real_loads(text, *args, **kwargs)

    monkeypatch.setattr(module.json, "loads", _loads)
    out = tmp_path / "out"
    build_scene({}, coverage, dense, poses, {}, None, out, params=SceneParams())
    assert (out / "dense.ply").read_bytes() == b"dense-bytes"
    assert (out / "flagged.ply").read_bytes() == b"flagged-bytes"
    assert (out / "sparse.ply").read_bytes() == b"sparse-bytes"


def _install_loader(monkeypatch: pytest.MonkeyPatch, module: str, class_name: str, seen: list[str]) -> None:
    mod = types.ModuleType(module)
    mod.__path__ = []
    mod.__package__ = module

    class _Model:
        @classmethod
        def from_pretrained(cls, source: str) -> _Model:
            seen.append(source)
            return cls()

        def to(self, device: object) -> _Model:
            del device
            return self

        def eval(self) -> _Model:
            return self

    setattr(mod, class_name, _Model)
    parts = module.split(".")
    for index in range(len(parts)):
        name = ".".join(parts[: index + 1])
        current = sys.modules.get(name)
        if name == module or current is None:
            current = mod if name == module else types.ModuleType(name)
            current.__path__ = []
            current.__package__ = name
            monkeypatch.setitem(sys.modules, name, current)
        if index:
            parent_name = ".".join(parts[:index])
            setattr(sys.modules[parent_name], parts[index], sys.modules[name])


def test_dense_loads_local_snapshots_and_falls_back_to_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SD-59: HSS_DENSE_WEIGHTS local dirs, hub repo id only when the dir is missing."""
    house = tmp_path / "house-weights"
    house.mkdir()
    monkeypatch.setenv("HOUSE_WEIGHTS", str(house))
    hf = tmp_path / "hf"
    monkeypatch.setenv("HSS_DENSE_WEIGHTS", str(hf))
    assert dense_params_from_env().resolved_weights() == hf
    monkeypatch.delenv("HSS_DENSE_WEIGHTS", raising=False)
    assert DenseParams().resolved_weights() == Path("/vol/hf")

    seen: list[str] = []
    _install_loader(monkeypatch, "pi3.models.pi3", "Pi3", seen)
    _install_loader(monkeypatch, "depth_anything_3.api", "DepthAnything3", seen)
    _install_loader(monkeypatch, "moge.model.v2", "MoGeModel", seen)
    load_pi3(hf)
    load_da3(hf)
    load_moge(hf)
    assert seen == [PI3_REPO, DA3_REPO, MOGE_WEIGHTS]

    folders = {
        PI3_REPO: hf / "yyfz233--Pi3",
        DA3_REPO: hf / "depth-anything--DA3-LARGE-1.1",
        MOGE_WEIGHTS: hf / "Ruicheng--moge-2-vits-normal",
    }
    for folder in folders.values():
        folder.mkdir(parents=True)
        (folder / "model.safetensors").write_bytes(b"not-a-real-weight")
    seen.clear()
    load_pi3(hf)
    load_da3(hf)
    load_moge(hf)
    assert seen == [
        str(folders[PI3_REPO]),
        str(folders[DA3_REPO]),
        str(folders[MOGE_WEIGHTS] / "model.safetensors"),
    ]
