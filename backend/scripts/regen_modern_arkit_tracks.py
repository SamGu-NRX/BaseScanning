#!/usr/bin/env python3
"""Rewrite lidar_modern / nonlidar_modern tracks onto a continuous walk.

The committed ETH3D packets integrated ARKit drift in DSLR filename order, which
jumps 25–30 m and leaves one facade non-LiDAR camera 6.4 m off. This reapplies
`packetgen.noise.draw_modern_walk` to the clean poses in `_truth/truth.json`,
keeps every other field, and refreshes the pose stream, feature points, taps
and plane anchors so they live in that same world.

Images, depth, timestamps and the 2018 / pose-free variants stay as they are.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

import numpy as np
from packetgen.conventions import column_major, from_column_major, pixel_ray_arkit, rotmat_to_quat_xyzw
from packetgen.devices import VARIANTS
from packetgen.noise import draw_modern_walk, load_profiles, rigid_residuals
from packetgen.packet import compose
from scipy.spatial.transform import Rotation, Slerp

FEATURE = np.dtype([("xyz", "<f4", (3,)), ("id", "<u8")])
MODERN = ("arkit-lidar", "arkit-nonlidar")
NOTE = (
    "Modern ARKit drift is integrated along a nearest-neighbour walk, not DSLR filename order. "
    "The draw is the first seed offset whose rigid RMS vs the clean poses is inside 0.065–0.22 m, "
    "with no camera more than 1 m off."
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pose(kf: dict) -> np.ndarray:
    return from_column_major(kf["pose"])


def _rigid(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mu_s, mu_d = src.mean(0), dst.mean(0)
    h = (src - mu_s).T @ (dst - mu_d)
    u, _s, vt = np.linalg.svd(h)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt = vt.copy()
        vt[-1] *= -1
        rotation = vt.T @ u.T
    return rotation, mu_d - rotation @ mu_s


def _apply_rigid(points: np.ndarray, rotation: np.ndarray, shift: np.ndarray) -> np.ndarray:
    return points @ rotation.T + shift


def _rewrite_features(path: Path, old: np.ndarray, new: np.ndarray) -> None:
    rec = np.fromfile(path, dtype=FEATURE)
    if len(rec) == 0:
        return
    xyz = rec["xyz"].astype(float)
    cam = (xyz - old[:3, 3]) @ old[:3, :3]
    rec["xyz"] = (cam @ new[:3, :3].T + new[:3, 3]).astype(np.float32)
    rec.tofile(path)


def _rewrite_tap(tap: dict, old: np.ndarray, new: np.ndarray, intrinsics: list[float]) -> None:
    origin, direction = pixel_ray_arkit(new, tuple(intrinsics), np.asarray(tap["pixel"], float))
    tap["rayOrigin"] = [round(float(v), 6) for v in origin]
    tap["rayDirection"] = [round(float(v), 8) for v in direction]
    hit = tap.get("hit")
    if not hit or "position" not in hit:
        return
    cam = (np.asarray(hit["position"], float) - old[:3, 3]) @ old[:3, :3]
    moved = cam @ new[:3, :3].T + new[:3, 3]
    hit["position"] = [round(float(v), 6) for v in moved]
    hit["distance"] = round(float(np.linalg.norm(moved - origin)), 4)


def _rewrite_planes(planes: list[dict], old_c: np.ndarray, new_c: np.ndarray) -> None:
    rotation, shift = _rigid(old_c, new_c)
    for plane in planes:
        transform = from_column_major(plane["transform"])
        transform[:3, :3] = rotation @ transform[:3, :3]
        transform[:3, 3] = rotation @ transform[:3, 3] + shift
        plane["transform"] = [round(float(v), 6) for v in column_major(transform)]
        if "center" in plane:
            centre = _apply_rigid(np.asarray(plane["center"], float), rotation, shift)
            plane["center"] = [round(float(v), 6) for v in centre]


def _write_arkit_stream(path: Path, keyframes: list[dict]) -> None:
    times = np.array([float(frame["timestamp"]) for frame in keyframes], float)
    centres = np.stack([_pose(frame)[:3, 3] for frame in keyframes])
    rotations = Rotation.from_matrix(np.stack([_pose(frame)[:3, :3] for frame in keyframes]))
    slerp = Slerp(times, rotations)
    intr = np.array([frame["intrinsics"] for frame in keyframes], float)
    step = 1.0 / 30.0
    grid = list(np.round(np.arange(times[0], times[-1], step), 4))
    grid.extend(float(np.round(t, 4)) for t in times)
    stamps = sorted(set(grid))
    if stamps[-1] < times[-1]:
        stamps.append(float(times[-1]))
    rows = ["t,epoch,tracking,tx,ty,tz,qx,qy,qz,qw,fx,fy,cx,cy"]
    knots = {float(np.round(t, 4)): frame for frame, t in zip(keyframes, times, strict=True)}
    for stamp in stamps:
        exact = knots.get(stamp)
        if exact is not None:
            pose = _pose(exact)
            centre = pose[:3, 3]
            quat = rotmat_to_quat_xyzw(pose[:3, :3])
            k_intr = exact["intrinsics"]
            tracking = exact["tracking"]
            epoch = exact["epoch"]
        else:
            centre = np.array([np.interp(stamp, times, centres[:, axis]) for axis in range(3)])
            quat = rotmat_to_quat_xyzw(slerp([stamp]).as_matrix()[0])
            k_intr = [float(np.interp(stamp, times, intr[:, axis])) for axis in range(4)]
            earlier = max(i for i, t in enumerate(times) if t <= stamp)
            tracking = keyframes[earlier]["tracking"]
            epoch = keyframes[earlier]["epoch"]
        rows.append(f"{stamp:.4f},{epoch},{tracking}," + ",".join(f"{float(v):.6f}" for v in (*centre, *quat, *k_intr)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(path, "wb", mtime=0) as handle:
        handle.write(("\n".join(rows) + "\n").encode())


def _refresh_hash(packet: dict, root: Path, rel: str) -> None:
    path = root / rel
    digest = _sha256(path)
    size = path.stat().st_size
    for entry in packet["files"]:
        if entry["path"] == rel:
            entry["sha256"] = digest
            entry["bytes"] = size
            return
    raise SystemExit(f"unlisted file {rel}")


def _variant(packet_name: str) -> int:
    return list(VARIANTS).index(packet_name)


def rewrite_house(root: Path) -> dict[str, dict[str, float]]:
    truth_path = root / "_truth" / "truth.json"
    truth = json.loads(truth_path.read_text())
    profiles = load_profiles()
    report: dict[str, dict[str, float]] = {}
    for name in MODERN:
        packet_path = root / f"packet.{name}.json"
        packet = json.loads(packet_path.read_text())
        frames = truth["variants"][name]["frames"]
        by_id = {frame["id"]: frame for frame in frames}
        rotations, centres = [], []
        for kf in packet["keyframes"]:
            pose = from_column_major(by_id[kf["id"]]["pose_true"])
            rotations.append(pose[:3, :3])
            centres.append(pose[:3, 3])
        r_true = np.stack(rotations)
        c_true = np.stack(centres)
        profile = profiles[VARIANTS[name]["profile"]]
        r_obs, c_obs, epochs, draw, _rng, offset = draw_modern_walk(
            r_true, c_true, profile, int(truth["seed"]), _variant(name)
        )
        old_centres = np.stack([_pose(kf)[:3, 3] for kf in packet["keyframes"]])
        for index, kf in enumerate(packet["keyframes"]):
            old = _pose(kf)
            new = compose(r_obs[index], c_obs[index])
            kf["pose"] = [round(float(v), 6) for v in column_major(new)]
            new = _pose(kf)
            feature = kf.get("features") or {}
            if feature.get("file"):
                _rewrite_features(root / feature["file"], old, new)
                _refresh_hash(packet, root, feature["file"])
            for tap in packet.get("taps") or []:
                if tap.get("keyframe") == kf["id"]:
                    _rewrite_tap(tap, old, new, kf["intrinsics"])
        _rewrite_planes(
            packet.get("planes") or [], old_centres, np.stack([_pose(kf)[:3, 3] for kf in packet["keyframes"]])
        )
        stream = packet["streams"]["arkitPoses"]["file"]
        _write_arkit_stream(root / stream, packet["keyframes"])
        _refresh_hash(packet, root, stream)
        notes = packet["source"].get("notes") or ""
        if NOTE not in notes:
            packet["source"]["notes"] = (notes + " " + NOTE).strip()
        pose_line = packet["source"]["synthesizedFields"][0]
        if "nearest-neighbour walk" not in pose_line:
            packet["source"]["synthesizedFields"][0] = pose_line + "; drift integrated on a nearest-neighbour walk"
        packet_path.write_text(json.dumps(packet, indent=1) + "\n")
        err = rigid_residuals(c_obs, c_true)
        for index, kf in enumerate(packet["keyframes"]):
            frame = by_id[kf["id"]]
            if int(epochs[index]) == 0:
                delta = _pose(kf)[:3, 3] - c_true[index]
                frame["position_error_m_vs_clean_world"] = float(np.linalg.norm(delta))
            else:
                frame["position_error_m_vs_clean_world"] = None
            frame["yaw_error_deg"] = float(draw.yaw_error_deg[index])
            frame["epoch"] = f"e{int(epochs[index]) + 1}"
        variant = truth["variants"][name]
        variant["scales"] = draw.scales
        variant["session_multiplier"] = draw.session_multiplier
        variant["reset_index"] = draw.reset_index
        variant["walk_seed_offset"] = offset
        report[name] = {
            "rigid_rms_m": float(np.sqrt(np.mean(err**2))),
            "max_residual_m": float(err.max()),
            "seed_offset": offset,
        }
        if not (0.065 <= report[name]["rigid_rms_m"] <= 0.22 and report[name]["max_residual_m"] <= 1.0):
            raise SystemExit(f"{root.name} {name} outside the phone band: {report[name]}")
    truth_path.write_text(json.dumps(truth, indent=1) + "\n")
    house_path = root / "house.json"
    if house_path.is_file():
        house = json.loads(house_path.read_text())
        house["notes"] = NOTE
        house_path.write_text(json.dumps(house, indent=1) + "\n")
    return report


def _pack(root: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    with tarfile.open(dest, "w") as tar:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            info = tar.gettarinfo(path, arcname=str(Path(root.name) / path.relative_to(root)))
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            with path.open("rb") as handle:
                tar.addfile(info, handle)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--house", action="append", required=True)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    for house_id in args.house:
        archive = args.archive_dir / f"{house_id}.tar"
        parts = sorted(args.archive_dir.glob(f"{house_id}.tar.part-*"))
        blob = args.work / f"{house_id}.tar"
        if archive.is_file():
            blob.write_bytes(archive.read_bytes())
        elif parts:
            blob.write_bytes(b"".join(part.read_bytes() for part in parts))
        else:
            raise SystemExit(f"missing archive for {house_id}")
        root = args.work / house_id
        if root.exists():
            shutil.rmtree(root)
        with tarfile.open(blob) as tar:
            tar.extractall(args.work, filter="data")
        report = rewrite_house(root)
        print(house_id, json.dumps(report))
        for name in MODERN:
            src = root / f"packet.{name}.json"
            dest = args.fixtures / house_id / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
        _pack(root, archive)
        for part in parts:
            part.unlink()
        print(f"wrote {archive} ({archive.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
