"""Twelve rendered frames of a textured wall. CPU, well under two minutes."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from server.common.geometry import column_major, from_column_major
from server.poses.packet_io import load_packet
from server.poses.params import PoseParams
from server.poses.run import run_poses

FLIP_Z = np.array([1.0, -1.0, -1.0])


def _render(path: Path, centre: np.ndarray, texture: np.ndarray) -> None:
    """A corner: front plane z=0 and side plane x=1.6, so the scene is not flat."""
    height, width = 360, 480
    fx = fy = 500.0
    cx, cy = width / 2, height / 2
    image = np.zeros((height, width, 3), np.uint8)
    ys, xs = np.mgrid[0:height, 0:width]
    direction = np.stack(((xs - cx) / fx, (ys - cy) / fy, np.ones_like(xs, float)), axis=-1)
    direction = direction * FLIP_Z
    # Front wall.
    t_front = np.divide(
        -centre[2], direction[..., 2], out=np.full(xs.shape, np.inf), where=np.abs(direction[..., 2]) > 1e-8
    )
    front_ok_t = np.isfinite(t_front)
    hit_f = centre + np.where(front_ok_t, t_front, 0.0)[..., None] * direction
    ok_f = (
        front_ok_t & (t_front > 0.05) & (hit_f[..., 0] > -0.4) & (hit_f[..., 0] < 1.6) & (np.abs(hit_f[..., 1]) < 0.7)
    )
    # Side wall, so the reconstruction has depth.
    t_side = np.divide(
        1.6 - centre[0], direction[..., 0], out=np.full(xs.shape, np.inf), where=np.abs(direction[..., 0]) > 1e-8
    )
    side_ok_t = np.isfinite(t_side)
    hit_s = centre + np.where(side_ok_t, t_side, 0.0)[..., None] * direction
    ok_s = side_ok_t & (t_side > 0.05) & (hit_s[..., 2] > -0.2) & (hit_s[..., 2] < 1.4) & (np.abs(hit_s[..., 1]) < 0.7)
    use_side = ok_s & (t_side < t_front)
    use_front = ok_f & ~use_side
    tex_h, tex_w, _ = texture.shape
    for mask, hit, u_src in (
        (use_front, hit_f, (hit_f[..., 0] + 0.4) / 2.0),
        (use_side, hit_s, hit_s[..., 2] / 1.6),
    ):
        u = np.zeros(xs.shape, dtype=int)
        v = np.zeros(xs.shape, dtype=int)
        u[mask] = np.clip((u_src[mask] * (tex_w - 1)).astype(int), 0, tex_w - 1)
        v[mask] = np.clip(((0.7 - hit[mask, 1]) / 1.4 * (tex_h - 1)).astype(int), 0, tex_h - 1)
        image[mask] = texture[v[mask], u[mask]]
    Image.fromarray(image).save(path)


def _wall_packet(root: Path) -> tuple[Path, list[dict]]:
    rng = np.random.default_rng(0)
    cells = rng.integers(0, 255, size=(24, 40, 3), dtype=np.uint8)
    texture = np.kron(cells, np.ones((8, 8, 1), np.uint8))
    root.mkdir()
    keyframes = []
    # Insert out of capture order so the mapper has to sort by timestamp.
    for i in (3, 0, 11, 1, 7, 2, 9, 4, 8, 5, 10, 6):
        centre = np.array([-0.1 + i * 0.07, 0.04 * ((i % 3) - 1), 1.05 + 0.025 * (i % 2)])
        name = f"{i:02d}.png"
        _render(root / name, centre, texture)
        pose = np.eye(4)
        pose[:3, 3] = centre
        keyframes.append(
            {
                "id": f"k{i:02d}",
                "img": name,
                "w": 480,
                "h": 360,
                "intrinsics": [500.0, 500.0, 240.0, 180.0],
                "intrinsicsSource": "arkit",
                "pose": column_major(pose),
                "timestamp": float(i),
                "tracking": "normal",
                "epoch": "e1",
            }
        )
    packet = {
        "packetId": "synthetic-wall",
        "session": {"tier": "arkit"},
        "epochs": [{"id": "e1"}],
        "keyframes": keyframes,
        "stills": [
            {"id": "s1", "purpose": "meter_close", "img": "01.png", "keyframe": "k01", "w": 480, "h": 360},
            {"id": "s2", "purpose": "meter_oblique", "img": "02.png", "keyframe": "k02", "w": 480, "h": 360},
        ],
        "taps": [{"id": "t1", "label": "meter", "keyframe": "k00", "pixel": [240, 180]}],
    }
    packet_path = root / "packet.json"
    packet_path.write_text(json.dumps(packet))
    return packet_path, keyframes


def _pose_blob(out: Path) -> bytes:
    return (out / "poses" / "cameras.json").read_bytes()


def test_synthetic_wall_registers(tmp_path: Path):
    packet_path, keyframes = _wall_packet(tmp_path / "packet")
    view = load_packet(packet_path)
    # Production extraction and matching stay at 4 threads. That count is
    # load-sensitive: a busy make ci compared 1,321 points to 1,323 and failed
    # the exact cameras.json check. Mapping is already one thread. This pair
    # uses one extract/match thread so the exact comparison is stable, and a
    # long deadline so the wall-clock timeout does not trip while the machine
    # is busy. The 180 s value is per run, checked after matching.
    assert PoseParams().num_threads == 4
    params = PoseParams(gpu=False, num_threads=1, max_image_size=480, sfm_max_frames=48, timeout_s=600, seed=0)
    report = run_poses(view, tmp_path / "out-a", params=params)
    assert report["frames_registered"] == 12
    counts = report["registration_counts"]
    assert counts["mapper"] + counts["post_registered"] + counts["carried"] == 12
    poses = tmp_path / "out-a" / "poses"
    assert (poses / "database.db").is_file()
    assert not (poses / "_database.db").exists()
    assert not list(poses.glob("*-wal"))
    assert not list(poses.glob("*-shm"))
    assert not (poses / "_images").exists()
    assert report["prior"]["count"] == 12
    assert report["params"]["random_seed"] == 0
    assert report["params"]["num_threads"] == 1
    assert report["params"]["mapper_threads"] == 1
    assert report["params"]["blas_threads"] == 1
    assert report["params"]["blas_coretype"] == "HASWELL"
    assert report["params"]["sift_matcher"] == "faiss"
    assert report["frame_budget"]["frames_dropped_quality"] == 0
    assert report["meter"]["source"] == "tap"
    assert report["closeups"]["meter_close"]["registered"]
    assert report["closeups"]["meter_oblique"]["registered"]
    assert report["arkit_fit"]["rms_m"] <= 0.05
    choice = report["model_choice"]
    assert choice["shipped"] == report["mode"]
    assert choice["triggers"]
    assert all("threshold" in row and "value" in row and "fired" in row for row in choice["triggers"])
    cameras = json.loads((tmp_path / "out-a" / "poses" / "cameras.json").read_text())
    by_id = {row["keyframe_id"]: row for row in cameras}
    for frame in keyframes:
        got = from_column_major(by_id[frame["id"]]["pose"])[:3, 3]
        want = from_column_major(frame["pose"])[:3, 3]
        assert np.linalg.norm(got - want) <= 0.02
    again = run_poses(view, tmp_path / "out-b", params=params)
    assert again["frames_registered"] == report["frames_registered"]
    assert again["quality"]["points3D"] == report["quality"]["points3D"]
    assert abs(again["quality"]["mean_reproj_px"] - report["quality"]["mean_reproj_px"]) <= 0.05
    assert abs(again["arkit_fit"]["rms_m"] - report["arkit_fit"]["rms_m"]) <= 1e-9
    assert abs(again["arkit_fit"]["scale_pct"] - report["arkit_fit"]["scale_pct"]) <= 1e-9
    assert again["warnings"] == report["warnings"]
    assert again["model_choice"]["shipped"] == report["model_choice"]["shipped"]
    assert again["model_choice"]["action"] == report["model_choice"]["action"]
    assert again["model_choice"]["swap_reasons"] == report["model_choice"]["swap_reasons"]
    assert _pose_blob(tmp_path / "out-a") == _pose_blob(tmp_path / "out-b")
