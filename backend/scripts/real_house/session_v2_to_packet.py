"""Convert a Measure Lab session v2 zip into a packet folder.

The zip holds ``session.json`` plus JPEG keyframes. This is the guided-flow
fallback. The packet is ``source.kind: replay`` and is not uploaded: Measure
Lab does not record ``imuRaw`` or 30 Hz ``arkitPoses``. Location fields in the
session are dropped. Exit 3 when any keyframe lacks a pose or intrinsics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
import zipfile
from pathlib import Path

MISSING_STREAMS = ["imuRaw", "arkitPoses"]

UNITS = {
    "length": "meters",
    "angle": "degrees",
    "time": "seconds of device uptime, the clock of ARFrame.timestamp and CMDeviceMotion.timestamp",
    "image": "pixels of the saved JPEG",
    "rotationRate": "radians per second",
    "acceleration": "g (9.80665 m/s^2)",
    "magneticField": "microtesla",
}

CONVENTIONS = {
    "world": "ARKit world frame with worldAlignment .gravity: right-handed, y up, origin at session start",
    "camera": "ARKit camera frame: +x right, +y up, looks along -z",
    "device": "Core Motion device frame",
    "pose": "camera-to-world 4x4, 16 numbers column-major, meters",
    "intrinsics": "[fx, fy, cx, cy] in pixels of the unrotated landscape image",
    "pixel": "[u, v] continuous, (0, 0) the top-left corner of the top-left pixel",
    "ray": "origin at the camera; direction a unit vector",
    "geodetic": "WGS84 degrees",
    "heading": "degrees clockwise from north",
}


def convert(session_zip: Path, out_dir: Path, tier: str) -> Path:
    if tier not in {"arkit", "arkit_lidar"}:
        raise ValueError("tier must be arkit or arkit_lidar")
    if not session_zip.is_file():
        raise FileNotFoundError(session_zip)
    with zipfile.ZipFile(session_zip) as archive:
        names = archive.namelist()
        session_name = next((name for name in names if name.endswith("session.json")), None)
        if session_name is None:
            raise FileNotFoundError("session.json")
        session = json.loads(archive.read(session_name))
        keyframes_in = session.get("keyframes") or []
        if not keyframes_in:
            raise FileNotFoundError("keyframes")
        for index, frame in enumerate(keyframes_in):
            if not _pose_ok(frame.get("pose")) or not _intrinsics_ok(frame.get("intrinsics")):
                raise FileNotFoundError(f"keyframe {frame.get('id', index)} lacks pose or intrinsics")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "keyframes").mkdir(exist_ok=True)
        files = []
        keyframes = []
        for index, frame in enumerate(keyframes_in):
            frame_id = str(frame.get("id") or f"k{index + 1:05d}")
            image_name = frame.get("img") or frame.get("image") or f"{frame_id}.jpg"
            member = _find_member(names, image_name)
            dest_rel = f"keyframes/{frame_id}.jpg"
            dest = out_dir / dest_rel
            if member is None:
                raise FileNotFoundError(image_name)
            data = archive.read(member)
            dest.write_bytes(data)
            files.append(
                {
                    "path": dest_rel,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "role": "keyframe",
                }
            )
            keyframes.append(
                {
                    "id": frame_id,
                    "img": dest_rel,
                    "w": frame.get("w"),
                    "h": frame.get("h"),
                    "intrinsics": frame["intrinsics"],
                    "pose": frame["pose"],
                    "timestamp": frame.get("timestamp"),
                    "tracking": frame.get("tracking", "normal"),
                    "epoch": frame.get("epoch", "e1"),
                    "reason": frame.get("reason", "motion"),
                }
            )
    packet = {
        "format": "house-capture-packet",
        "formatVersion": "0.4",
        "packetId": str(uuid.uuid4()),
        "createdAt": session.get("createdAt") or "1970-01-01T00:00:00Z",
        "units": UNITS,
        "conventions": CONVENTIONS,
        "source": {
            "kind": "replay",
            "notes": ["Measure Lab session v2 fallback", *MISSING_STREAMS],
        },
        "session": {
            "id": session.get("id") or str(uuid.uuid4()),
            "tier": tier,
            "worldAlignment": "gravity",
            "videoFormat": session.get("videoFormat") or {"imageResolution": [1920, 1440], "framesPerSecond": 60},
            "lidarAvailable": tier == "arkit_lidar",
            "sceneDepthEnabled": tier == "arkit_lidar",
        },
        "epochs": session.get("epochs") or [{"id": "e1", "startTime": 0.0, "reason": "sessionStart"}],
        "keyframes": keyframes,
        "files": files,
        "taps": session.get("taps") or [],
    }
    out_path = out_dir / "packet.json"
    out_path.write_text(json.dumps(packet, indent=2) + "\n")
    return out_path


def _pose_ok(pose: object) -> bool:
    return isinstance(pose, list) and len(pose) == 16 and all(isinstance(value, (int, float)) for value in pose)


def _intrinsics_ok(intrinsics: object) -> bool:
    return (
        isinstance(intrinsics, list)
        and len(intrinsics) == 4
        and all(isinstance(value, (int, float)) for value in intrinsics)
    )


def _find_member(names: list[str], image_name: str) -> str | None:
    leaf = Path(image_name).name
    for name in names:
        if name.endswith("/" + leaf) or name == leaf or name.endswith(image_name):
            return name
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure Lab session v2 zip to packet v0.4")
    parser.add_argument("--zip", dest="session_zip", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--tier", required=True, choices=["arkit", "arkit_lidar"])
    args = parser.parse_args(argv)
    try:
        path = convert(args.session_zip, args.out, args.tier)
    except FileNotFoundError as exc:
        print(f"missing input: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
