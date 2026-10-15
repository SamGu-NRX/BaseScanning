"""Tiny on-disk packets for unit tests."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from server.packet.conventions import column_major


def identity_pose(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> list[float]:
    matrix = np.eye(4)
    matrix[:3, 3] = [x, y, z]
    return column_major(matrix)


def jpeg(path: Path, size: tuple[int, int] = (1920, 1440)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (40, 80, 120)).save(path, quality=85)


def _file(path: Path, root: Path, role: str) -> dict:
    data = path.read_bytes()
    return {
        "path": str(path.relative_to(root)),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "role": role,
    }


def write_arkit(root: Path, *, n: int = 2, tier: str = "arkit") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    w, h = 1920, 1440
    fx = fy = 1440.0
    for i in range(n):
        jpeg(root / f"keyframes/k{i:05d}.jpg", (w, h))
    jpeg(root / "stills/s1.jpg", (w, h))
    rows = ["t,ax,ay,az,gx,gy,gz"]
    for i in range(5):
        rows.append(f"{i * 0.01},0,-1,0,0,0,0")
    imu = root / "streams/imuRaw.csv.gz"
    imu.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(imu, "wt") as handle:
        handle.write("\n".join(rows) + "\n")
    pose_rows = ["t,epoch,tracking,tx,ty,tz,qx,qy,qz,qw,fx,fy,cx,cy"]
    for i in range(n):
        pose_rows.append(f"{100 + i},e1,normal,{i * 0.4},0,0,0,0,0,1,{fx},{fy},{w / 2},{h / 2}")
    poses = root / "streams/arkitPoses.csv.gz"
    with gzip.open(poses, "wt") as handle:
        handle.write("\n".join(pose_rows) + "\n")
    files = []
    keyframes = []
    for i in range(n):
        rel = root / f"keyframes/k{i:05d}.jpg"
        files.append(_file(rel, root, "keyframe"))
        pose = identity_pose(i * 0.4, 0, 0)
        keyframes.append(
            {
                "id": f"k{i:05d}",
                "img": f"keyframes/k{i:05d}.jpg",
                "w": w,
                "h": h,
                "intrinsics": [fx, fy, w / 2, h / 2],
                "pose": pose,
                "timestamp": 100.0 + i,
                "tracking": "normal",
                "epoch": "e1",
                "reason": "motion",
                "exposure": {"duration": 0.001, "iso": 50},
            }
        )
    files.append(_file(root / "stills/s1.jpg", root, "still"))
    files.append(_file(imu, root, "stream"))
    files.append(_file(poses, root, "stream"))
    packet = {
        "format": "house-capture-packet",
        "formatVersion": "0.4",
        "packetId": "pkt-unit-test-0001",
        "createdAt": "2026-09-26T18:00:00Z",
        "units": {"length": "meters", "angle": "degrees", "time": "s", "image": "px"},
        "conventions": {
            "world": "arkit",
            "camera": "arkit",
            "pose": "column-major",
            "intrinsics": "px",
            "pixel": "top-left",
        },
        "source": {"kind": "device"},
        "session": {
            "id": "sess",
            "startedAt": "2026-09-26T18:00:00Z",
            "startedAtUptime": 100.0,
            "appVersion": "0",
            "deviceModel": "iPhone",
            "systemVersion": "18",
            "tier": tier,
            "lidarAvailable": tier == "arkit_lidar",
            "sceneDepthEnabled": False,
            "worldAlignment": "gravity",
            "videoFormat": {"imageResolution": [w, h]},
        },
        "epochs": [{"id": "e1", "startTime": 100.0, "reason": "sessionStart"}],
        "keyframes": keyframes,
        "stills": [
            {
                "id": "s1",
                "purpose": "meter_close",
                "img": "stills/s1.jpg",
                "w": w,
                "h": h,
                "timestamp": 101.0,
                "orientation": 1,
                "keyframe": "k00000",
            }
        ],
        "taps": [],
        "tracking": [{"time": 100.0, "state": "normal", "epoch": "e1"}],
        "streams": {
            "imuRaw": {
                "file": "streams/imuRaw.csv.gz",
                "columns": ["t", "ax", "ay", "az", "gx", "gy", "gz"],
                "rateHz": 100,
            },
            "arkitPoses": {
                "file": "streams/arkitPoses.csv.gz",
                "columns": "t,epoch,tracking,tx,ty,tz,qx,qy,qz,qw,fx,fy,cx,cy".split(","),
                "rateHz": 10,
            },
        },
        "scaleReference": {
            "meterCloseUp": {"captured": True, "still": "s1", "arkitDistanceM": 1.0, "viewAngleDeg": 5},
            "ext": {
                "method": "a9-meter-model",
                "obliqueCloseUp": {"captured": False},
                "doorViews": [{"frame": "k00000", "kind": "entry_door", "box": [10, 10, 40, 40]}],
                "doorOnMeterSide": True,
            },
        },
        "files": files,
    }
    path = root / "packet.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    return path
