import json
import shutil
import socket
from pathlib import Path

import numpy as np
import pytest

from server.contracts.jobs import StageContext
from server.scale import run as stage_run
from server.scale.lidar import align_lidar
from server.scale.packet_view import ScaleError, resolve_packet
from server.scale.params import ScaleParams
from server.scale.run import run_scale
from server.scale.validate import validate_file

SEED = Path(__file__).resolve().parent / "fixtures" / "meter_models.seed.v1.json"


class _Cam:
    def __init__(self, image, pose, intrinsics, w, h):
        self.image = image
        self.keyframe_id = image
        self.pose = pose
        self.intrinsics = intrinsics
        self.w = w
        self.h = h


def _metric_scene():
    xs = np.linspace(-0.3, 0.3, 30)
    ys = np.linspace(-0.2, 0.2, 20)
    points = np.array([(x, y, -2.0) for x in xs for y in ys], float)
    intrinsics = [80.0, 80.0, 32.0, 24.0]
    cameras = []
    depths = {}
    for index in range(6):
        pose = np.eye(4)
        pose[:3, 3] = (0.02 * index, 0.0, 0.0)
        image = f"k{index}"
        cameras.append(_Cam(image, pose, intrinsics, 64, 48))
        depth = np.zeros((48, 64), np.float32)
        conf = np.zeros((48, 64), np.uint8)
        local = (points - pose[:3, 3]) @ pose[:3, :3]
        z = -local[:, 2]
        u = intrinsics[0] * local[:, 0] / z + intrinsics[2]
        v = -intrinsics[1] * local[:, 1] / z + intrinsics[3]
        ui = np.rint(u).astype(int)
        vi = np.rint(v).astype(int)
        inside = (ui >= 0) & (ui < 64) & (vi >= 0) & (vi < 48)
        depth[vi[inside], ui[inside]] = z[inside]
        conf[vi[inside], ui[inside]] = 2
        depths[image] = (depth, conf)
    return points, cameras, depths


def test_lidar_scale_recovers_a_similarity():
    points, cameras, depths = _metric_scene()
    base = align_lidar(points, cameras, depths, min_conf=2, min_pairs=50, max_m=5.0, seed=0)
    assert base.applied and base.pairs >= 200 and base.frames >= 5
    for factor in (0.85, 1.15):
        moved = []
        for camera in cameras:
            pose = np.array(camera.pose, copy=True)
            pose[:3, 3] *= factor
            moved.append(_Cam(camera.image, pose, camera.intrinsics, camera.w, camera.h))
        scaled = align_lidar(points * factor, moved, depths, min_conf=2, min_pairs=50, max_m=5.0, seed=0)
        assert scaled.applied
        assert scaled.scale * factor == pytest.approx(base.scale, rel=0.005)


def _write_poses(directory: Path, cameras: list[dict], report: dict) -> None:
    directory.mkdir(parents=True)
    (directory / "cameras.json").write_text(json.dumps(cameras))
    (directory / "registration_report.json").write_text(json.dumps(report))


def _pose(x=0.0, y=0.0, z=0.0) -> list[float]:
    matrix = np.eye(4)
    matrix[:3, 3] = (x, y, z)
    return matrix.T.reshape(-1).tolist()


def _packet(tier: str, model: str, captured: bool) -> dict:
    frames = []
    for index in range(4):
        frames.append(
            {
                "id": f"k{index}",
                "w": 64,
                "h": 48,
                "intrinsics": [40, 40, 32, 24],
                "pose": np.eye(4).tolist(),
                "epoch": index,
            }
        )
        frames[-1]["pose"][2][3] = 0.1 * index
    return {
        "session": {"tier": tier, "deviceModel": model, "lidarAvailable": False, "sceneDepthEnabled": False},
        "keyframes": frames,
        "stills": [
            {"id": "s1", "purpose": "meter_close", "img": "s1.jpg", "w": 64, "h": 48},
            {"id": "s2", "purpose": "meter_oblique", "img": "s2.jpg", "w": 64, "h": 48},
        ],
        "scaleReference": {"meterCloseUp": {"captured": captured}, "meterIdentity": {}},
        "taps": [{"label": "meter"}],
    }


def test_nonlidar_and_pose_free_fallthrough(tmp_path: Path):
    params = ScaleParams(table_path=SEED)
    poses = tmp_path / "poses"
    cameras = [
        {
            "image": f"k{index}",
            "keyframe_id": f"k{index}",
            "pose": _pose(0.2 * index, 0, 0),
            "intrinsics": [40, 40, 32, 24],
            "w": 64,
            "h": 48,
        }
        for index in range(4)
    ]
    _write_poses(
        poses,
        cameras,
        {"source_frame": "arkit_world:e1", "closeups": {}, "meter": {"source": "none"}, "lidar_frames": 0},
    )
    modern_packet = _packet("arkit", "iPhone14,5", False)
    old_packet = _packet("arkit", "iPhone8,1", False)
    for packet in (modern_packet, old_packet):
        for index, frame in enumerate(packet["keyframes"]):
            matrix = np.eye(4)
            matrix[:3, 3] = (0.2 * index, 0.0, 0.0)
            frame["pose"] = matrix.tolist()
    modern = run_scale(modern_packet, tmp_path, poses, tmp_path / "modern", params=params)
    assert modern.source == "arkit"
    assert modern.status == "unverified"
    assert modern.reason == "scale_unresolved"
    assert modern.reason_detail == "meter_closeup_missing"
    assert 0.08 <= modern.rel_bound_95 <= 0.11
    assert modern.views_needed[0].kind == "close_up"
    assert modern.member_actions == ["retake_meter_closeups"]
    assert modern.table_version == "1.0.0"
    validate_file(tmp_path / "modern" / "scale.json")
    old = run_scale(old_packet, tmp_path, poses, tmp_path / "old", params=params)
    assert 0.25 <= old.rel_bound_95 <= 0.33
    empty = tmp_path / "none"
    _write_poses(
        empty, [], {"source_frame": "colmap_model", "closeups": {}, "meter": {"source": "none"}, "lidar_frames": 0}
    )
    free = run_scale(
        {
            "session": {"tier": "photo_only", "deviceModel": "iPhone14,5"},
            "keyframes": [],
            "scaleReference": {"meterCloseUp": {"captured": False}},
        },
        tmp_path,
        empty,
        tmp_path / "free",
        params=params,
    )
    assert free.source == "none"
    assert free.scale_factor is None
    assert free.confidence == "none"
    assert free.reason_detail == "no_scale_source"
    validate_file(tmp_path / "free" / "scale.json")


def test_directory_resolves_packet_json_only(tmp_path: Path):
    (tmp_path / "packet.arkit-lidar.json").write_text("{}")
    with pytest.raises(ScaleError):
        resolve_packet(tmp_path)
    (tmp_path / "packet.json").write_text("{}")
    assert resolve_packet(tmp_path).name == "packet.json"


def test_stage_has_no_identify_meter_entry():
    import server.scale as scale

    assert not hasattr(scale, "identify_meter")
    assert scale.STAGE == "scale"


def test_run_survives_a_blocked_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def _boom(*_args, **_kwargs):
        raise OSError("network blocked")

    monkeypatch.setattr(socket, "socket", _boom)
    work = tmp_path / "work"
    poses = work / "poses"
    _write_poses(
        poses, [], {"source_frame": "colmap_model", "closeups": {}, "meter": {"source": "none"}, "lidar_frames": 0}
    )
    block = run_scale(
        {
            "session": {"tier": "photo_only", "deviceModel": "iPhone14,5"},
            "keyframes": [],
            "scaleReference": {"meterCloseUp": {"captured": False}},
        },
        tmp_path,
        poses,
        tmp_path / "out",
        params=ScaleParams(table_path=SEED),
    )
    assert block.reason_detail == "no_scale_source"
    ctx = StageContext(
        capture_id="c",
        run_id="r",
        stage="scale",
        attempt=1,
        branch="image",
        flow="photo",
        tier="photo_only",
        work_dir=work,
        inputs={"packet_dir": str(tmp_path), "run_control": str(tmp_path / "run_control.json")},
        deadline_at="2026-09-27T00:00:00Z",
        heartbeat=lambda _substep: None,
        vlm_budget_usd=0.0,
        storage=None,
        state=None,
        settings=None,
    )
    (tmp_path / "packet.json").write_text(
        json.dumps(
            {
                "session": {"tier": "photo_only", "deviceModel": "iPhone14,5"},
                "keyframes": [],
                "scaleReference": {"meterCloseUp": {"captured": False}},
            }
        )
    )
    result = stage_run(ctx)
    assert "scale/scale.json" in result.artifacts


def _arkit_cameras():
    from server.scale.apply import CameraRecord

    cameras = []
    for index, x in enumerate((-0.4, 0.0, 0.4)):
        pose = np.eye(4)
        pose[:3, 3] = (x, 0.0, 0.0)
        cameras.append(CameraRecord(f"k{index}", None, pose, [100, 100, 50, 50], 100, 100))
    return cameras


def _gzip_pose_stream(path: Path, rows: list[str]) -> None:
    import gzip

    header = "t,epoch,tracking,tx,ty,tz,qx,qy,qz,qw,fx,fy,cx,cy\n"
    path.write_bytes(gzip.compress((header + "\n".join(rows) + "\n").encode()))


def test_arkit_stream_pairs_by_timestamp_and_ignores_a_missing_one(tmp_path):
    from server.scale.arkit_gate import arkit_gate

    _gzip_pose_stream(tmp_path / "arkit_poses.csv.gz", ["0.0,e1,normal,0.0,0.0,0.0,0,0,0,1,100,100,50,50"])
    keyframes = []
    for index, x in enumerate((-0.4, 0.0, 0.4)):
        pose = np.eye(4)
        pose[:3, 3] = (2 * x, 0.0, 0.0)
        frame = {"id": f"k{index}", "epoch": "e1", "pose": pose.tolist()}
        if index == 0:
            frame["timestamp"] = 0.0
        keyframes.append(frame)
    packet = {
        "session": {"tier": "arkit_lidar", "deviceModel": "iPhone 15 Pro"},
        "streams": {"arkitPoses": {"file": "arkit_poses.csv.gz", "rateHz": 10}},
        "keyframes": keyframes,
    }
    gate = arkit_gate(_arkit_cameras(), packet, tmp_path, 2.0, band_pct=10)
    assert gate.available
    assert gate.s_arkit_per_source == pytest.approx(2.0, rel=1e-6)
    assert gate.passed


def test_gzip_pose_stream_is_not_mixed_with_embedded_poses(tmp_path):
    from server.scale.arkit_gate import arkit_gate

    rows = []
    keyframes = []
    for index, (stamp, x) in enumerate(zip((1.0, 2.0, 3.0), (-0.4, 0.0, 0.4), strict=True)):
        rows.append(f"{stamp},e1,normal,{1.5 * x},0.0,0.0,0,0,0,1,100,100,50,50")
        pose = np.eye(4)
        pose[:3, 3] = (9 * x, 0.0, 0.0)
        keyframes.append({"id": f"k{index}", "epoch": "e1", "timestamp": stamp, "pose": pose.tolist()})
    _gzip_pose_stream(tmp_path / "arkit_poses.csv.gz", rows)
    packet = {
        "session": {"tier": "arkit_lidar", "deviceModel": "iPhone 15 Pro"},
        "streams": {"arkitPoses": {"file": "arkit_poses.csv.gz", "columns": ["t", "epoch"], "rateHz": 10}},
        "keyframes": keyframes,
    }
    gate = arkit_gate(_arkit_cameras(), packet, tmp_path, 1.5, band_pct=10)
    assert gate.s_arkit_per_source == pytest.approx(1.5, rel=1e-6)
    assert gate.passed


def test_run_scale_reads_a_gzipped_arkit_pose_stream(tmp_path: Path):
    rows = []
    keyframes = []
    cameras = []
    for index, x in enumerate((-0.4, 0.0, 0.4)):
        rows.append(f"{float(index)},e1,normal,{1.5 * x},0,0,0,0,0,1,40,40,32,24")
        pose = np.eye(4)
        pose[:3, 3] = (x, 0.0, 0.0)
        keyframes.append(
            {
                "id": f"k{index}",
                "epoch": "e1",
                "timestamp": float(index),
                "w": 64,
                "h": 48,
                "intrinsics": [40, 40, 32, 24],
                "pose": pose.tolist(),
            }
        )
        cameras.append(
            {
                "image": f"k{index}",
                "keyframe_id": f"k{index}",
                "pose": pose.tolist(),
                "intrinsics": [40, 40, 32, 24],
                "w": 64,
                "h": 48,
            }
        )
    _gzip_pose_stream(tmp_path / "arkit_poses.csv.gz", rows)
    poses = tmp_path / "poses"
    _write_poses(
        poses,
        cameras,
        {"source_frame": "arkit_world:e1", "closeups": {}, "meter": {"source": "none"}, "lidar_frames": 0},
    )
    packet = {
        "session": {"tier": "arkit", "deviceModel": "iPhone15,2", "lidarAvailable": False},
        "keyframes": keyframes,
        "streams": {"arkitPoses": {"file": "arkit_poses.csv.gz", "rateHz": 10, "columns": ["t", "epoch", "tx"]}},
        "scaleReference": {"meterCloseUp": {"captured": False}},
    }
    block = run_scale(packet, tmp_path, poses, tmp_path / "out", params=ScaleParams(table_path=SEED))
    assert block.arkit_gate is not None
    assert block.arkit_gate.s_arkit_per_source == pytest.approx(1.5, rel=1e-4)
    validate_file(tmp_path / "out" / "scale.json")


def test_lidar_stands_when_the_meter_closeup_is_missing(tmp_path: Path):
    points, cameras, depths = _metric_scene()
    poses = tmp_path / "poses"
    poses.mkdir()
    _write_ascii_ply(poses / "sparse.ply", points)
    pose_rows = []
    keyframes = []
    for camera in cameras:
        pose_rows.append(
            {
                "image": camera.image,
                "keyframe_id": camera.image,
                "pose": camera.pose.tolist(),
                "intrinsics": camera.intrinsics,
                "w": camera.w,
                "h": camera.h,
            }
        )
        depth, conf = depths[camera.image]
        depth_name = f"depth/{camera.image}.bin"
        conf_name = f"depth/{camera.image}.u8"
        (tmp_path / "depth").mkdir(exist_ok=True)
        (tmp_path / depth_name).write_bytes(np.asarray(depth, dtype="<f4").tobytes())
        (tmp_path / conf_name).write_bytes(np.asarray(conf, dtype=np.uint8).tobytes())
        keyframes.append(
            {
                "id": camera.image,
                "w": camera.w,
                "h": camera.h,
                "intrinsics": camera.intrinsics,
                "pose": camera.pose.tolist(),
                "epoch": "e1",
                "timestamp": 0.0,
                "depth": {"file": depth_name, "confidenceFile": conf_name, "w": camera.w, "h": camera.h},
            }
        )
    (poses / "cameras.json").write_text(json.dumps(pose_rows))
    (poses / "registration_report.json").write_text(
        json.dumps({"source_frame": "arkit_world:e1", "closeups": {}, "meter": {"source": "none"}, "lidar_frames": 6})
    )
    packet = {
        "session": {"tier": "arkit_lidar", "deviceModel": "iPhone15,2", "lidarAvailable": True},
        "keyframes": keyframes,
        "scaleReference": {"meterCloseUp": {"captured": False}},
    }
    block = run_scale(packet, tmp_path, poses, tmp_path / "out", params=ScaleParams(table_path=SEED))
    assert block.source == "lidar"
    assert block.status in {"verified", "single_reference"}
    assert block.reason_detail is None
    assert block.member_actions == []
    assert block.scale_factor == pytest.approx(1.0, rel=0.02)
    validate_file(tmp_path / "out" / "scale.json")


def test_truedepth_uses_the_cover_diameter_not_a_center_depth_ratio(tmp_path: Path):
    import cv2

    from server.scale.run import _truedepth_source

    frames = []
    for index in range(10):
        depth_name = f"td{index}.bin"
        image_name = f"td{index}.png"
        depth = np.full((120, 120), 4.0, np.float32)
        yy, xx = np.mgrid[0:120, 0:120]
        inside = (xx - 60) ** 2 + (yy - 60) ** 2 <= 18**2
        depth[inside] = 1.2
        (tmp_path / depth_name).write_bytes(depth.tobytes())
        image = np.full((120, 120), 20, np.uint8)
        cv2.circle(image, (60, 60), 18, 240, 1)
        cv2.imwrite(str(tmp_path / image_name), image)
        frames.append(
            {
                "depth": depth_name,
                "image": image_name,
                "w": 120,
                "h": 120,
                "intrinsics": [50.0, 50.0, 60.0, 60.0],
                "timestamp": float(index),
            }
        )
    scan = {
        "captured": True,
        "depthDataAccuracy": "absolute",
        "depthDataQuality": "high",
        "frames": frames,
    }
    packet = {"scaleReference": {"ext": {"trueDepthScan": scan}}}
    # diameter = 2 * 18 * 1.2 / 50 = 0.864 m; source diameter 0.432 m → scale 2.
    meter = type("Meter", (), {"radius_source": 0.216, "depth_source": 0.05})()
    source = _truedepth_source(packet, tmp_path, meter)
    assert source is not None
    # A center-patch median would see the 4 m background and land near 6.7.
    assert np.exp(source.log_obs) == pytest.approx(2.0, rel=0.08)
    assert float(np.exp(source.log_obs)) < 3.0
    assert _truedepth_source(packet, tmp_path, meter, enabled=False) is None
    scan["depthDataQuality"] = "low"
    assert _truedepth_source(packet, tmp_path, meter) is None


def test_run_scale_loads_a_meter_tap_box_without_an_explicit_argument(tmp_path: Path):
    pose = np.eye(4)
    poses = tmp_path / "poses"
    _write_poses(
        poses,
        [
            {
                "image": "k0",
                "keyframe_id": "k0",
                "pose": pose.tolist(),
                "intrinsics": [1000.0, 1000.0, 500.0, 400.0],
                "w": 1000,
                "h": 800,
            }
        ],
        {"source_frame": "arkit_world:e1", "closeups": {}, "meter": {"source": "none"}, "lidar_frames": 0},
    )
    packet = {
        "session": {"tier": "arkit", "deviceModel": "iPhone15,2", "lidarAvailable": False},
        "keyframes": [{"id": "k0", "img": "k0.jpg"}],
        "scaleReference": {"meterCloseUp": {"captured": True, "still": "s1"}},
        "taps": [
            {
                "label": "meter",
                "keyframe": "k0",
                "pixel": [500.0, 400.0],
                "hit": {"distance": 2.0},
            }
        ],
    }
    block = run_scale(packet, tmp_path, poses, tmp_path / "out", params=ScaleParams(table_path=SEED))
    box = block.evidence.boxes_px["k0"]
    assert abs(box[2] - box[0]) == pytest.approx(500.0)
    (poses.parent / "objects").mkdir()
    (poses.parent / "objects" / "objects.json").write_text(
        json.dumps(
            {
                "format": "hss-objects/v0",
                "objects": [{"class": "meter_can", "views": [{"image": "k0", "box_px": [1, 2, 80, 90]}]}],
            }
        )
    )
    blocked = run_scale(packet, tmp_path, poses, tmp_path / "out2", params=ScaleParams(table_path=SEED))
    assert blocked.evidence.boxes_px["k0"] == [1.0, 2.0, 80.0, 90.0]


def test_read_ply_xyz_reads_a_ws04_colour_ply(tmp_path: Path):
    from server.scale.packet_view import read_ply_xyz

    xyz = np.array([[1.25, -2.5, 3.0], [0.0, 0.5, -1.0], [4.0, 8.0, 0.25]], float)
    path = tmp_path / "sparse.ply"
    _write_ws04_ply(
        path,
        xyz,
        rgb=np.array([[10, 20, 30], [1, 2, 3], [255, 0, 128]], np.uint8),
        n_views=np.array([3, 1, 7], np.int32),
    )
    got = read_ply_xyz(path)
    assert got.shape == xyz.shape
    assert got == pytest.approx(xyz)
    raw = path.read_bytes()
    _header, _, rest = raw.partition(b"end_header\n")
    misread = np.frombuffer(rest, dtype="<f4", count=xyz.size).reshape(-1, 3)
    assert not np.allclose(misread, xyz)
    path.write_bytes(raw[: len(raw) // 2])
    assert read_ply_xyz(path).shape == (0, 3)


def test_lidar_flatness_rejects_discontinuous_short_edges():
    """Short edge pixels outnumber the flat truth, so a median without the filter moves."""

    w, h = 64, 48
    depth = np.full((h, w), 2.0, np.float32)
    conf = np.full((h, w), 2, np.uint8)
    depth[10, 10] = 1.9
    fx, fy, cx, cy = 80.0, 80.0, 32.0, 24.0
    pose = np.eye(4)
    camera = _Cam("k0", pose, [fx, fy, cx, cy], w, h)

    def at(u: float, v: float) -> np.ndarray:
        z = 2.0
        return np.array([(u - cx) * z / fx, -(v - cy) * z / fy, -z], float)

    flat_uv = [(u, v) for u in range(20, 40, 2) for v in range(16, 32, 2)]
    flat = np.stack([at(u, v) for u, v in flat_uv])
    short = np.repeat(at(10, 10)[None, :], len(flat) + 40, axis=0)
    points = np.concatenate([flat, short])
    alignment = align_lidar(points, [camera], {"k0": (depth, conf)}, min_conf=2, min_pairs=50, max_m=5.0, seed=0)
    assert alignment.applied
    assert alignment.scale == pytest.approx(1.0, abs=0.005)
    assert alignment.pairs == len(flat)


def test_execute_reads_ws04_poses_from_the_work_dir(tmp_path: Path):
    """I-12: poses are work_dir/poses/<WS-00 §3.9 name>, not a ctx.inputs path."""

    from server.scale.packet_view import load_points, load_tracks

    xyz = np.array([[0.1, 0.2, -2.0], [-0.3, 0.0, -2.2], [0.4, -0.1, -1.8]], float)
    work = tmp_path / "work"
    poses = work / "poses"
    _write_poses(
        poses,
        [
            {
                "image": "k00001",
                "keyframe_id": "k00001",
                "pose": np.eye(4).reshape(-1).tolist(),
                "intrinsics": [80.0, 80.0, 32.0, 24.0],
                "w": 64,
                "h": 48,
            },
            {
                "image": "k00002",
                "keyframe_id": "k00002",
                "pose": np.eye(4).reshape(-1).tolist(),
                "intrinsics": [80.0, 80.0, 32.0, 24.0],
                "w": 64,
                "h": 48,
            },
        ],
        {"source_frame": "colmap_model", "closeups": {}, "meter": {"source": "none"}, "lidar_frames": 0},
    )
    _write_ws04_ply(poses / "sparse.ply", xyz)
    n = len(xyz)
    # CSR: point i is seen by cameras.json rows 0 and 1. Row i of the ply is CSR point i.
    np.savez(
        poses / "tracks.npz",
        indptr=np.arange(0, 2 * n + 1, 2, dtype=np.int64),
        indices=np.tile(np.array([0, 1], np.int32), n),
        point_ids=np.arange(n, dtype=np.int64),
    )
    assert load_points(poses) == pytest.approx(xyz)
    tracks = load_tracks(poses)
    assert tracks["k00001"] == pytest.approx(xyz)
    assert tracks["k00002"] == pytest.approx(xyz)
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    (packet_dir / "packet.json").write_text(
        json.dumps(
            {
                "session": {"tier": "photo_only", "deviceModel": "iPhone14,5"},
                "keyframes": [],
                "scaleReference": {"meterCloseUp": {"captured": False}},
            }
        )
    )
    decoy = tmp_path / "decoy-poses"
    _write_poses(decoy, [], {"source_frame": "colmap_model", "closeups": {}, "meter": {"source": "none"}})
    ctx = StageContext(
        capture_id="c",
        run_id="r",
        stage="scale",
        attempt=1,
        branch="image",
        flow="photo",
        tier="photo_only",
        work_dir=work,
        inputs={"packet_dir": str(packet_dir), "poses": str(decoy), "poses/cameras.json": str(decoy / "cameras.json")},
        deadline_at="2026-09-27T00:00:00Z",
        heartbeat=lambda _substep: None,
        vlm_budget_usd=0.0,
        storage=None,
        state=None,
        settings=None,
    )
    result = stage_run(ctx)
    assert "scale/scale.json" in result.artifacts
    missing = StageContext(
        capture_id="c",
        run_id="r",
        stage="scale",
        attempt=1,
        branch="image",
        flow="photo",
        tier="photo_only",
        work_dir=tmp_path / "empty-work",
        inputs={"packet_dir": str(packet_dir), "poses": str(poses)},
        deadline_at="2026-09-27T00:00:00Z",
        heartbeat=lambda _substep: None,
        vlm_budget_usd=0.0,
        storage=None,
        state=None,
        settings=None,
    )
    with pytest.raises(ScaleError, match="poses_artifact_missing:cameras.json"):
        stage_run(missing)


def test_execute_reads_meter_identity_from_the_work_dir(tmp_path: Path):
    """D11: reads.json under the work dir resolves CENTRON. ctx.inputs is only the fallback."""

    mini = json.loads(
        (Path(__file__).resolve().parents[3] / "data/fixtures/packets/mini-arkit/packet.json").read_text()
    )
    mini["scaleReference"]["meterIdentity"] = {}
    packet_dir = tmp_path / "mini-arkit"
    packet_dir.mkdir()
    (packet_dir / "packet.json").write_text(json.dumps(mini))
    reads_src = Path(__file__).resolve().parent / "fixtures" / "reads" / "m28.reads.json"
    table = Path(__file__).resolve().parents[3] / "data/meter-models/meter_models.v1.json"
    cameras = [
        {
            "image": frame["id"],
            "keyframe_id": frame["id"],
            "pose": frame["pose"],
            "intrinsics": frame["intrinsics"],
            "w": frame["w"],
            "h": frame["h"],
        }
        for frame in mini["keyframes"]
    ]
    report = {
        "source_frame": "arkit_world:e1",
        "closeups": {
            "meter_close": {"frame": "k00003", "registered": True},
            "meter_oblique": {"frame": "k00004", "registered": True},
        },
        "meter": {"source": "closeup"},
        "lidar_frames": 0,
    }

    def _ctx(work: Path, inputs: dict) -> StageContext:
        return StageContext(
            capture_id="c",
            run_id="r",
            stage="scale",
            attempt=1,
            branch="image",
            flow="photo",
            tier="arkit",
            work_dir=work,
            inputs=inputs,
            deadline_at="2026-09-27T00:00:00Z",
            heartbeat=lambda _substep: None,
            vlm_budget_usd=0.0,
            storage=None,
            state=None,
            settings=type("Settings", (), {"scale": ScaleParams(table_path=table)})(),
        )

    work = tmp_path / "work"
    _write_poses(work / "poses", cameras, report)
    staged = work / "reads" / "reads.json"
    staged.parent.mkdir(parents=True)
    shutil.copyfile(reads_src, staged)
    stage_run(_ctx(work, {"packet_dir": str(packet_dir), "run_control": "{}"}))
    block = json.loads((work / "scale" / "scale.json").read_text())
    assert block["table_entry"] == "itron-centron-c1s-poly"
    assert block["reason_detail"] == "scale_reference_edge_not_found"

    early_work = tmp_path / "early-work"
    _write_poses(early_work / "poses", cameras, report)
    early = early_work / "early" / "meter_identity.json"
    early.parent.mkdir(parents=True)
    shutil.copyfile(reads_src, early)
    stage_run(_ctx(early_work, {"packet_dir": str(packet_dir)}))
    early_block = json.loads((early_work / "scale" / "scale.json").read_text())
    assert early_block["table_entry"] == "itron-centron-c1s-poly"

    unread = tmp_path / "unread"
    _write_poses(unread / "poses", cameras, report)
    stage_run(_ctx(unread, {"packet_dir": str(packet_dir)}))
    missed = json.loads((unread / "scale" / "scale.json").read_text())
    assert missed["reason_detail"] == "meter_model_unread"
    assert missed["table_entry"] is None

    fallback = tmp_path / "fallback"
    _write_poses(fallback / "poses", cameras, report)
    stage_run(_ctx(fallback, {"packet_dir": str(packet_dir), "reads/reads.json": str(reads_src)}))
    from_inputs = json.loads((fallback / "scale" / "scale.json").read_text())
    assert from_inputs["table_entry"] == "itron-centron-c1s-poly"


def test_lidar_too_few_pairs_stays_on_the_estimate_when_the_meter_is_missing(tmp_path: Path):
    """M2: a short LiDAR cloud behind a missing close-up is a lidar estimate, not a new reason_detail."""

    w, h = 16, 16
    depth = np.zeros((h, w), np.float32)
    conf = np.zeros((h, w), np.uint8)
    depth[4:7, 4:7] = 2.0
    conf[4:7, 4:7] = 2
    (tmp_path / "depth").mkdir()
    (tmp_path / "depth/k0.bin").write_bytes(np.asarray(depth, dtype="<f4").tobytes())
    (tmp_path / "depth/k0.u8").write_bytes(np.asarray(conf, dtype=np.uint8).tobytes())
    cameras = [
        {
            "image": f"k{index}",
            "keyframe_id": f"k{index}",
            "pose": _pose(0.2 * index),
            "intrinsics": [40.0, 40.0, 8.0, 8.0],
            "w": w,
            "h": h,
        }
        for index in range(4)
    ]
    poses = tmp_path / "poses"
    _write_poses(
        poses,
        cameras,
        {"source_frame": "arkit_world:e1", "closeups": {}, "meter": {"source": "none"}, "lidar_frames": 1},
    )
    keyframes = []
    for index in range(4):
        frame = {
            "id": f"k{index}",
            "w": w,
            "h": h,
            "intrinsics": [40.0, 40.0, 8.0, 8.0],
            "pose": _pose(0.2 * index),
            "epoch": "e1",
            "timestamp": float(index),
        }
        if index == 0:
            frame["depth"] = {"file": "depth/k0.bin", "confidenceFile": "depth/k0.u8", "w": w, "h": h}
        keyframes.append(frame)
    points = np.array([[0.0, 0.0, -2.0]], float)
    _write_ascii_ply(poses / "sparse.ply", points)
    packet = {
        "session": {"tier": "arkit_lidar", "deviceModel": "iPhone15,2", "lidarAvailable": True},
        "keyframes": keyframes,
        "scaleReference": {"meterCloseUp": {"captured": False}},
    }
    block = run_scale(packet, tmp_path, poses, tmp_path / "out", params=ScaleParams(table_path=SEED))
    assert block.reason_detail == "meter_closeup_missing"
    assert block.source == "arkit"
    lidar_rows = [row for row in block.estimates if row.kind == "lidar"]
    assert len(lidar_rows) == 1
    assert lidar_rows[0].used is False
    assert lidar_rows[0].reason == "lidar_too_few_pairs"
    validate_file(tmp_path / "out" / "scale.json")


def _write_ws04_ply(
    path: Path, xyz: np.ndarray, rgb: np.ndarray | None = None, n_views: np.ndarray | None = None
) -> None:
    """Binary little-endian PLY: float x y z, uchar red green blue, int32 n_views."""

    cloud = np.asarray(xyz, np.float32)
    n = len(cloud)
    colours = np.zeros((n, 3), np.uint8) if rgb is None else np.asarray(rgb, np.uint8)
    views = np.ones(n, np.int32) if n_views is None else np.asarray(n_views, np.int32)
    dtype = np.dtype(
        [
            ("x", "<f4"),
            ("y", "<f4"),
            ("z", "<f4"),
            ("red", "u1"),
            ("green", "u1"),
            ("blue", "u1"),
            ("n_views", "<i4"),
        ]
    )
    arr = np.empty(n, dtype=dtype)
    arr["x"], arr["y"], arr["z"] = cloud[:, 0], cloud[:, 1], cloud[:, 2]
    arr["red"], arr["green"], arr["blue"] = colours[:, 0], colours[:, 1], colours[:, 2]
    arr["n_views"] = views
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {n}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "property int32 n_views\n"
        "end_header\n"
    )
    path.write_bytes(header.encode("ascii") + arr.tobytes())


def _write_ascii_ply(path: Path, points: np.ndarray) -> None:
    lines = [
        "ply",
        "format ascii 1.0",
        f"element vertex {len(points)}",
        "property float x",
        "property float y",
        "property float z",
        "end_header",
    ]
    for point in points:
        lines.append(f"{point[0]} {point[1]} {point[2]}")
    path.write_text("\n".join(lines) + "\n")
