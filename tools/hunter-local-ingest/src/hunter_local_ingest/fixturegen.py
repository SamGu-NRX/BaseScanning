"""Generate a deterministic, fully synthetic capture plan.

Everything this writes is synthetic by construction: seeded noise files
under synthetic/, two seeded CSV streams, and a packet whose
``source.kind`` is ``synthetic``. No photographs, homeowner data or
credentials. The same seed reproduces the same plan byte for byte; a
different seed changes every file's bytes, not only the packet id.

The create body and the packet follow the published types in
ios/HouseScanKit/Sources/HouseScanKit (Upload/CaptureAPI.swift
CreateRequest, Packet/Packet04.swift Packet, Packet/Packet04Streams.swift
column names) at alpha 263827ef. Keyframes are empty: the files are noise,
not images, and the packet says so rather than pretending.

The runner's loader (plan.py) re-verifies every digest written here.

Usage:
  hunter-gen-plan --seed 1 --out synthetic-plan.json
  python fixtures/gen_synthetic_packet.py --seed 1 --out synthetic-plan.json  (repo checkout)
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path

POSE_COLUMNS = [
    "t",
    "epoch",
    "tracking",
    "tx",
    "ty",
    "tz",
    "qx",
    "qy",
    "qz",
    "qw",
    "fx",
    "fy",
    "cx",
    "cy",
]
IMU_COLUMNS = ["t", "ax", "ay", "az", "gx", "gy", "gz"]


def noise_bytes(seed: int, path: str, n: int) -> bytes:
    """SHA-256 in counter mode over (seed, path): deterministic, seed-dependent."""
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(f"{seed}:{path}:{counter}".encode()).digest()
        counter += 1
    return bytes(out[:n])


def _jitter(seed: int, stream: str, row: int) -> float:
    """A small seeded offset in [0, 1e-3), so stream bytes vary with the seed."""
    value = int.from_bytes(hashlib.sha256(f"{seed}:{stream}:{row}".encode()).digest()[:4], "big")
    return value / 2**32 * 1e-3


def _gzip(text: str) -> bytes:
    # Level 0 stores the bytes uncompressed, so the output does not depend
    # on the zlib build; mtime 0 keeps the header fixed.
    return gzip.compress(text.encode("utf-8"), compresslevel=0, mtime=0)


def pose_stream(seed: int) -> bytes:
    rows = [",".join(POSE_COLUMNS)]
    for i in range(120):  # 2 s at 60 Hz, above the contract's 10 Hz floor
        t = 5000.0 + i / 60.0
        x = (t - 5000.0) * 0.1 + _jitter(seed, "pose", i)
        rows.append(
            ",".join(
                [
                    f"{t:.6f}",
                    "e1",
                    "normal",
                    f"{x:.6f}",
                    "1.5",
                    "0.0",
                    "0.0",
                    "0.0",
                    "0.0",
                    "1.0",
                    "1445.0",
                    "1445.0",
                    "960.0",
                    "720.0",
                ]
            )
        )
    return _gzip("\n".join(rows) + "\n")


def imu_stream(seed: int) -> bytes:
    rows = [",".join(IMU_COLUMNS)]
    for i in range(200):  # 2 s at 100 Hz, above the contract's 50 Hz floor
        t = 5000.0 + i / 100.0
        rows.append(
            ",".join(
                [
                    f"{t:.6f}",
                    f"{_jitter(seed, 'ax', i):.6f}",
                    "-1.0",
                    "0.0",
                    "0.0",
                    f"{_jitter(seed, 'gy', i):.6f}",
                    "0.0",
                ]
            )
        )
    return _gzip("\n".join(rows) + "\n")


def build(seed: int, mesh_count: int, mesh_kb: int) -> dict:
    packet_id = f"synthetic-{seed:06d}"
    contents: list[tuple[str, str, str, bytes]] = []
    for i in range(mesh_count):
        path = f"synthetic/scan{i:02d}.bin"
        contents.append(
            (path, "other", "application/octet-stream", noise_bytes(seed, path, mesh_kb * 1024))
        )
    contents.append(("streams/arkit_poses.csv.gz", "stream", "application/gzip", pose_stream(seed)))
    contents.append(("streams/imu_raw.csv.gz", "stream", "application/gzip", imu_stream(seed)))

    files = []
    entries = []
    for path, role, content_type, data in contents:
        sha = hashlib.sha256(data).hexdigest()
        files.append(
            {
                "path": path,
                "role": role,
                "contentType": content_type,
                "bytes": len(data),
                "sha256": sha,
                "md5": base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode(),
                "content_hex": data.hex(),
            }
        )
        entries.append({"path": path, "bytes": len(data), "sha256": sha, "role": role})

    # Packet04.Packet with every non-optional field; optional ones are left
    # out, as the client's encoder leaves out nil.
    packet = {
        "format": "house-capture-packet",
        "formatVersion": "0.4",
        "packetId": packet_id,
        "createdAt": "2026-09-01T00:00:00Z",
        "units": {
            "length": "meters",
            "angle": "degrees",
            "time": "seconds of device uptime (ARFrame.timestamp and Core Motion timestamp)",
            "image": "pixels of the saved, unrotated landscape JPEG",
            "rotationRate": "radians per second",
            "acceleration": "g (9.80665 m/s^2)",
        },
        "conventions": {
            "world": "ARKit world, worldAlignment .gravity: right-handed, +y up, origin where "
            "the epoch started",
            "camera": "ARKit camera: +x right, +y up in the unrotated landscape image, "
            "looking along -z",
            "pose": "camera-to-world 4x4, 16 numbers column by column (simd_float4x4 layout), "
            "meters",
            "intrinsics": "[fx, fy, cx, cy] in pixels of the saved, unrotated landscape image",
            "pixel": "[u, v] continuous: (0, 0) is the top-left corner of the top-left pixel, "
            "v grows down",
        },
        "source": {"kind": "synthetic"},
        "session": {
            "id": f"session-{packet_id}",
            "startedAt": "2026-09-01T00:00:00Z",
            "startedAtUptime": 5000.0,
            "appVersion": "0.0 (synthetic)",
            "deviceModel": "synthetic-device",
            "systemVersion": "0.0",
            "tier": "arkit",
            "lidarAvailable": False,
            "sceneDepthEnabled": False,
            "worldAlignment": "gravity",
            "videoFormat": {"imageResolution": [1920, 1440]},
        },
        "epochs": [{"id": "e1", "startTime": 5000.0, "reason": "sessionStart"}],
        "keyframes": [],
        "tracking": [],
        "streams": {
            "imuRaw": {"file": "streams/imu_raw.csv.gz", "columns": IMU_COLUMNS, "rateHz": 100.0},
            "arkitPoses": {
                "file": "streams/arkit_poses.csv.gz",
                "columns": POSE_COLUMNS,
                "rateHz": 60.0,
            },
        },
        "scaleReference": {
            "meterCloseUp": {"captured": False},
            "ext": {
                "method": "a9-meter-model",
                "obliqueCloseUp": {"captured": False},
                "doorViews": [],
            },
        },
        "files": entries,
    }
    packet_bytes = json.dumps(packet, sort_keys=True, separators=(",", ":")).encode("utf-8")

    document = {
        "format": "hunter-local-ingest-plan",
        "format_version": 2,
        "generator": {
            "name": "gen_synthetic_packet",
            "seed": seed,
            "mesh_count": mesh_count,
            "mesh_kb": mesh_kb,
            "sha256_of_plan": None,
        },
        "plan": {
            "packet_id": packet_id,
            # CaptureAPI.CreateRequest
            "create_request": {
                "packetId": packet_id,
                "formatVersion": "0.4",
                "tier": "arkit",
                "flow": "guided",
                "device": {
                    "model": "synthetic-device",
                    "systemVersion": "0.0",
                    "appVersion": "0.0 (synthetic)",
                },
            },
            "files": files,
            "packet_hex": packet_bytes.hex(),
            "packet_sha256": hashlib.sha256(packet_bytes).hexdigest(),
        },
    }
    blob = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    document["generator"]["sha256_of_plan"] = hashlib.sha256(blob).hexdigest()
    return document


def write_plan(out: Path, seed: int = 1, mesh_count: int = 3, mesh_kb: int = 64) -> dict:
    document = build(seed, mesh_count, mesh_kb)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(prog="hunter-gen-plan", description=__doc__)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", default="synthetic-plan.json")
    parser.add_argument("--mesh-count", type=int, default=3)
    parser.add_argument("--mesh-kb", type=int, default=64, help="per-file size in KiB")
    args = parser.parse_args()
    if not (0 <= args.seed < 2**31) or not (1 <= args.mesh_count <= 62) or args.mesh_kb < 1:
        parser.error("seed must be 0..2^31-1, mesh-count 1..62, mesh-kb >= 1")

    out = Path(args.out)
    document = write_plan(out, args.seed, args.mesh_count, args.mesh_kb)
    print(f"wrote {out.name} (plan sha256 {document['generator']['sha256_of_plan']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
