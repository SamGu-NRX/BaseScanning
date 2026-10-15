"""Build a v0.4 packet for one LaMAR segment and run the pose stage on CPU.

A6 numbers need every frame in the mapper and ``max_image_size`` 1920.
Images and trajectories live under ``~/lamar`` and are not committed.

    uv run python scripts/reproduce_a6.py --segment 2022-06-17_10.43.29_s0 --out /tmp/ws04/a6-s0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import numpy as np  # noqa: E402
from scipy.spatial.transform import Rotation  # noqa: E402

from server.common.geometry import FLIP_YZ, column_major, from_column_major, umeyama  # noqa: E402
from server.poses.packet_io import load_packet  # noqa: E402
from server.poses.params import PoseParams  # noqa: E402
from server.poses.run import run_poses  # noqa: E402


def _root() -> Path:
    env = os.environ.get("HSS_LAMAR_ROOT")
    if env:
        return Path(env)
    shared = Path.home() / "hss-data" / "lamar"
    if (shared / "gt_ios.json").is_file():
        return shared
    return Path.home() / "lamar"


ROOT = _root()


def _arkit(session: str) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    path = ROOT / "raw" / session / "trajectories.txt"
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        ts = parts[0]
        qw, qx, qy, qz, tx, ty, tz = (float(v) for v in parts[2:9])
        rotation = Rotation.from_quat([qx, qy, qz, qw]).as_matrix()
        out[ts] = (rotation, np.array([tx, ty, tz], float))
    return out


def _intrinsics(session: str) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    path = ROOT / "raw" / session / "sensors.txt"
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        out[parts[0].replace("cam_phone_", "")] = [float(v) for v in parts[4:10]]
    return out


def _pose(rotation_wc: np.ndarray, centre: np.ndarray) -> list[float]:
    """OpenCV cam-to-world -> ARKit column-major, translation stays the centre."""
    pose = np.eye(4)
    pose[:3, :3] = rotation_wc @ FLIP_YZ
    pose[:3, 3] = centre
    return column_major(pose)


def build_packet(segment_id: str, dest: Path) -> Path:
    segments = json.loads(
        Path(__file__).resolve().parents[1].joinpath("recon/a6-iphone-quality/results/segments.json").read_text()
    )
    seg = next(item for item in segments if item["id"] == segment_id)
    session = seg["session"]
    arkit = _arkit(session)
    intr = _intrinsics(session)
    images = ROOT / "raw" / session / "raw_data" / "images"
    dest.mkdir(parents=True, exist_ok=True)
    keyframes = []
    for index, ts in enumerate(seg["frames"]):
        src = images / f"{ts}.jpg"
        if not src.is_file():
            raise SystemExit(f"missing frame {src}")
        link = dest / f"{ts}.jpg"
        if not link.exists():
            link.symlink_to(src)
        w, h, fx, fy, cx, cy = intr[ts]
        rotation, centre = arkit[ts]
        keyframes.append(
            {
                "id": f"k{index:04d}",
                "img": f"{ts}.jpg",
                "w": int(w),
                "h": int(h),
                "intrinsics": [fx, fy, cx, cy],
                "intrinsicsSource": "arkit",
                "pose": _pose(rotation, centre),
                "timestamp": float(ts),
                "tracking": "normal",
                "epoch": "e1",
            }
        )
    packet = {
        "packetId": f"lamar-{segment_id}",
        "session": {"tier": "arkit"},
        "epochs": [{"id": "e1"}],
        "keyframes": keyframes,
        "stills": [],
        "taps": [],
        "ext": {"hss": {"derived": {"flow": "guided", "branch": "image", "primaryEpoch": "e1"}}},
    }
    path = dest / "packet.json"
    path.write_text(json.dumps(packet))
    return path


def score_gt(cameras_path: Path, session: str) -> dict:
    """Sim(3) of reconstructed centres onto the LaMAR GT centres.

    Scale percent is ``(1/s - 1) * 100``, the A6 convention. Rigid RMS is the
    same fit with scale held at 1.
    """
    gt_path = ROOT / "gt_ios.json"
    if not gt_path.is_file():
        return {"gt": "missing"}
    gt = json.loads(gt_path.read_text())
    cameras = json.loads(cameras_path.read_text())
    src, dst = [], []
    for row in cameras:
        if row.get("pose_source") == "arkit" and row.get("points3D", 0) == 0:
            continue
        key = f"{session}/{row['image']}.jpg"
        record = gt.get(key)
        if record is None:
            continue
        rotation = np.asarray(record["R"], float)
        translation = np.asarray(record["t"], float)
        src.append(from_column_major(row["pose"])[:3, 3])
        dst.append(-rotation.T @ translation)
    if len(src) < 3:
        return {"gt_matched": len(src)}
    src_a = np.stack(src)
    dst_a = np.stack(dst)
    scale, _, _ = umeyama(src_a, dst_a, with_scale=True)
    _, rotation, translation = umeyama(src_a, dst_a, with_scale=False)
    err = (src_a @ rotation.T) + translation - dst_a
    rms = float(np.sqrt((err**2).sum(axis=1).mean()))
    pct = (1.0 / scale - 1.0) * 100.0 if scale else 0.0
    return {"gt_matched": len(src), "scale_pct": pct, "rigid_rms_m": rms}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reproduce one A6 segment on CPU")
    parser.add_argument("--segment", default="2022-06-17_10.43.29_s0")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-image-size", type=int, default=1920)
    args = parser.parse_args(argv)
    packet_path = build_packet(args.segment, args.out / "packet")
    view = load_packet(packet_path)
    params = PoseParams(
        gpu=False,
        num_threads=4,
        sfm_max_frames=max(99, len(view.frames)),
        max_image_size=args.max_image_size,
        seed=0,
        timeout_s=7200,
    )
    report = run_poses(view, args.out, params=params, heartbeat=print)
    session = args.segment.rsplit("_s", 1)[0]
    session = "ios_" + session if not session.startswith("ios_") else session
    summary = {
        "segment": args.segment,
        "registered": report["frames_registered"],
        "given": report["frames_given"],
        "status": report["status"],
        "mode": report["mode"],
        "arkit_fit": report["arkit_fit"],
        "mapper_s": report["timings_s"]["mapper_s"],
        "total_s": report["timings_s"]["total_s"],
        "warnings": report["warnings"],
        "random_seed": report["params"]["random_seed"],
        "num_threads": report["params"]["num_threads"],
        "model_choice": report.get("model_choice"),
    }
    summary.update(score_gt(args.out / "poses" / "cameras.json", session))
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
