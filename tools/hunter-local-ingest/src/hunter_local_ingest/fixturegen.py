"""Generate a deterministic, fully synthetic capture plan.

Everything this writes is synthetic by construction: seeded noise files
under synthetic/, two seeded CSV streams, and a packet whose
``source.kind`` is ``synthetic``. No photographs, homeowner data or
credentials. The same seed reproduces the same plan byte for byte; a
different seed changes every file's bytes, not only the packet id.

The create body and the packet follow the published types in
ios/HouseScanKit/Sources/HouseScanKit (Upload/CaptureAPI.swift
CreateRequest, Packet/Packet04.swift Packet, Packet/Packet04Streams.swift
column names) at alpha 263827ef. Keyframes are synthetic: small seeded
baseline JPEGs a pipeline can decode (a seeded 8x8 tile mosaic, written
by the package's own minimal encoder), declared with the keyframe role,
so an arkit-tier packet carries what the published contract requires
(the first REAL_API run: the server refused an arkit packet without
keyframes).

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
KEYFRAME_COUNT = 3
MAX_MESH_COUNT = 59  # 59 meshes + 2 streams + 3 keyframes stays within MAX_FILES=64.

# Keyframe geometry: the packet's declared keyframe w/h, a multiple of the
# 8x8 JPEG block so no MCU is padded.
KEYFRAME_WIDTH = 64
KEYFRAME_HEIGHT = 48
# Every quantization value is 16, so a DC-only tile of quantized level L
# decodes back to the exact pixel value 2L + 128: the JPEG DC coefficient
# carries the block average AFTER the standard level shift (pixels are
# centered on 128 before the DCT), so F(0,0) = 8 * (value - 128).
_JPEG_QUANT = 16
# Every keyframe is padded to this fixed byte count with a COM (comment)
# segment right after APP0, so the plan's per-file byte counts stay
# seed-independent (the receipts contract pins non-stream file sizes).
_KEYFRAME_JPEG_BYTES = 768

# Annex K luminance huffman tables (JPEG spec, Figures K.3 and K.5): the
# BITS counts per code length 1..16 and the symbols in order.
_DC_BITS = (0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0)
_DC_VALS = tuple(range(12))
_AC_BITS = (0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7D)
_AC_VALS = (
    0x01,
    0x02,
    0x03,
    0x00,
    0x04,
    0x11,
    0x05,
    0x12,
    0x21,
    0x31,
    0x41,
    0x06,
    0x13,
    0x51,
    0x61,
    0x07,
    0x22,
    0x71,
    0x14,
    0x32,
    0x81,
    0x91,
    0xA1,
    0x08,
    0x23,
    0x42,
    0xB1,
    0xC1,
    0x15,
    0x52,
    0xD1,
    0xF0,
    0x24,
    0x33,
    0x62,
    0x72,
    0x82,
    0x09,
    0x0A,
    0x16,
    0x17,
    0x18,
    0x19,
    0x1A,
    0x25,
    0x26,
    0x27,
    0x28,
    0x29,
    0x2A,
    0x34,
    0x35,
    0x36,
    0x37,
    0x38,
    0x39,
    0x3A,
    0x43,
    0x44,
    0x45,
    0x46,
    0x47,
    0x48,
    0x49,
    0x4A,
    0x53,
    0x54,
    0x55,
    0x56,
    0x57,
    0x58,
    0x59,
    0x5A,
    0x63,
    0x64,
    0x65,
    0x66,
    0x67,
    0x68,
    0x69,
    0x6A,
    0x73,
    0x74,
    0x75,
    0x76,
    0x77,
    0x78,
    0x79,
    0x7A,
    0x83,
    0x84,
    0x85,
    0x86,
    0x87,
    0x88,
    0x89,
    0x8A,
    0x92,
    0x93,
    0x94,
    0x95,
    0x96,
    0x97,
    0x98,
    0x99,
    0x9A,
    0xA2,
    0xA3,
    0xA4,
    0xA5,
    0xA6,
    0xA7,
    0xA8,
    0xA9,
    0xAA,
    0xB2,
    0xB3,
    0xB4,
    0xB5,
    0xB6,
    0xB7,
    0xB8,
    0xB9,
    0xBA,
    0xC2,
    0xC3,
    0xC4,
    0xC5,
    0xC6,
    0xC7,
    0xC8,
    0xC9,
    0xCA,
    0xD2,
    0xD3,
    0xD4,
    0xD5,
    0xD6,
    0xD7,
    0xD8,
    0xD9,
    0xDA,
    0xE1,
    0xE2,
    0xE3,
    0xE4,
    0xE5,
    0xE6,
    0xE7,
    0xE8,
    0xE9,
    0xEA,
    0xF1,
    0xF2,
    0xF3,
    0xF4,
    0xF5,
    0xF6,
    0xF7,
    0xF8,
    0xF9,
    0xFA,
)


def _canonical_codes(bits: tuple[int, ...], symbols: tuple[int, ...]) -> dict[int, tuple[int, int]]:
    """Canonical huffman codes (JPEG spec C.2): (code, length) per symbol."""
    codes: dict[int, tuple[int, int]] = {}
    code = 0
    index = 0
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            codes[symbols[index]] = (code, length)
            index += 1
            code += 1
        code <<= 1
    assert index == len(symbols), "table bits must match its symbols"
    return codes


_DC_CODES = _canonical_codes(_DC_BITS, _DC_VALS)
_AC_CODES = _canonical_codes(_AC_BITS, _AC_VALS)


class _BitWriter:
    """MSB-first bit writer with JPEG byte stuffing (0xFF -> 0xFF 0x00)."""

    def __init__(self) -> None:
        self.out = bytearray()
        self._acc = 0
        self._n = 0

    def write(self, code: int, length: int) -> None:
        for shift in range(length - 1, -1, -1):
            self._acc = (self._acc << 1) | ((code >> shift) & 1)
            self._n += 1
            if self._n == 8:
                self.out.append(self._acc)
                if self._acc == 0xFF:
                    self.out.append(0x00)
                self._acc = 0
                self._n = 0

    def flush(self) -> bytes:
        if self._n:
            self._acc = (self._acc << (8 - self._n)) | ((1 << (8 - self._n)) - 1)
            self.out.append(self._acc)
            if self._acc == 0xFF:
                self.out.append(0x00)
        return bytes(self.out)


def _segment(marker: int, payload: bytes) -> bytes:
    # The two length bytes count themselves: length = len(payload) + 2.
    return bytes((0xFF, marker)) + (len(payload) + 2).to_bytes(2, "big") + payload


def keyframe_tiles(seed: int, path: str, width: int, height: int) -> list[list[int]]:
    """One even pixel value (32..254) per 8x8 tile, seed- and path-dependent."""
    blocks_x, blocks_y = width // 8, height // 8
    raw = noise_bytes(seed, f"{path}:tiles", blocks_x * blocks_y)
    return [
        [2 * (16 + raw[y * blocks_x + x] % 112) for x in range(blocks_x)] for y in range(blocks_y)
    ]


def keyframe_jpeg(
    seed: int, path: str, width: int = KEYFRAME_WIDTH, height: int = KEYFRAME_HEIGHT
) -> bytes:
    """A small, valid, deterministic baseline JPEG.

    A grayscale 8x8 tile mosaic whose tile values come from the seeded byte
    stream: the image decodes in any reader and still changes byte for byte
    with the seed and path. The encoder is DC-only per tile (every AC
    coefficient is zero), so the entropy stream is the DC symbol, its
    amplitude bits and an end-of-block code per block.
    """
    if width % 8 or height % 8:
        raise ValueError("keyframe dimensions must be multiples of the 8x8 JPEG block")
    tiles = keyframe_tiles(seed, path, width, height)
    blocks_x, blocks_y = width // 8, height // 8

    out = bytearray(b"\xff\xd8")  # SOI
    # JFIF APP0: version 1.01, no density units, square aspect.
    out += _segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    # The rest of the stream, built first so the COM padding below can be
    # sized to the exact final byte count.
    body = bytearray()
    # DQT: table 0, eight-bit precision, every value _JPEG_QUANT.
    body += _segment(0xDB, b"\x00" + bytes([_JPEG_QUANT]) * 64)
    # SOF0: baseline, one component, no subsampling, table 0.
    frame = b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x01\x01\x11\x00"
    body += _segment(0xC0, frame)
    # DHT: the standard luminance DC and AC tables.
    body += _segment(0xC4, b"\x00" + bytes(_DC_BITS) + bytes(_DC_VALS))
    body += _segment(0xC4, b"\x10" + bytes(_AC_BITS) + bytes(_AC_VALS))
    # SOS: component 1, DC table 0 and AC table 0, spectral 0..63, point 0.
    body += _segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")
    writer = _BitWriter()
    predictor = 0
    for y in range(blocks_y):
        for x in range(blocks_x):
            # Quantized DC level of the block average, level shift included:
            # exact for even tile values ((value - 128) is even, halved once).
            level = (tiles[y][x] - 128) // 2
            diff = level - predictor
            predictor = level
            if diff == 0:
                code, length = _DC_CODES[0]
                writer.write(code, length)
            else:
                category = abs(diff).bit_length()
                code, length = _DC_CODES[category]
                writer.write(code, length)
                amplitude = diff if diff > 0 else diff + (1 << category) - 1
                writer.write(amplitude, category)
            eob, eob_length = _AC_CODES[0x00]
            writer.write(eob, eob_length)
    body += writer.flush()
    body += b"\xff\xd9"  # EOI
    # COM padding to the fixed byte count: a comment segment every reader
    # ignores, so the file length does not depend on the seeded entropy.
    pad = _KEYFRAME_JPEG_BYTES - len(out) - len(body) - 4
    if pad < 0:
        raise ValueError("keyframe overflows its fixed byte budget")
    out += _segment(0xFE, b"\x00" * pad)
    out += body
    return bytes(out)


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
    if not (1 <= mesh_count <= MAX_MESH_COUNT):
        raise ValueError(f"mesh_count must be 1..{MAX_MESH_COUNT}")
    packet_id = f"synthetic-{seed:06d}"
    contents: list[tuple[str, str, str, bytes]] = []
    for i in range(mesh_count):
        path = f"synthetic/scan{i:02d}.bin"
        contents.append(
            (path, "other", "application/octet-stream", noise_bytes(seed, path, mesh_kb * 1024))
        )
    contents.append(("streams/arkit_poses.csv.gz", "stream", "application/gzip", pose_stream(seed)))
    contents.append(("streams/imu_raw.csv.gz", "stream", "application/gzip", imu_stream(seed)))
    for i in range(KEYFRAME_COUNT):
        path = f"keyframes/kf{i + 1:02d}.jpg"
        contents.append(
            (
                path,
                "keyframe",
                "image/jpeg",
                keyframe_jpeg(seed, path, KEYFRAME_WIDTH, KEYFRAME_HEIGHT),
            )
        )

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
        "keyframes": [
            {
                "id": f"kf{i + 1:02d}",
                "img": f"keyframes/kf{i + 1:02d}.jpg",
                "w": 64,
                "h": 48,
                "intrinsics": [48.07, 48.07, 31.9867, 23.98],
                "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0.02 * i, -0.01 * i, 0.0, 1],
                "timestamp": 5000.0 + i / 15.0,
                "tracking": "normal",
                "epoch": "e1",
                "reason": "motion",
            }
            for i in range(KEYFRAME_COUNT)
        ],
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
    if (
        not (0 <= args.seed < 2**31)
        or not (1 <= args.mesh_count <= MAX_MESH_COUNT)
        or args.mesh_kb < 1
    ):
        parser.error(f"seed must be 0..2^31-1, mesh-count 1..{MAX_MESH_COUNT}, mesh-kb >= 1")

    out = Path(args.out)
    document = write_plan(out, args.seed, args.mesh_count, args.mesh_kb)
    print(f"wrote {out.name} (plan sha256 {document['generator']['sha256_of_plan']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
