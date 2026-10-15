#!/usr/bin/env python3
"""Build the committed mini-arkit packet from the Appendix B example.

Deterministic: solid 64×48 JPEGs, feature files of 20 bytes per point, and
two-row gzip CSVs. `packet.json` is variant-less (SD-43).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path

from PIL import Image

WIDTH = 64
HEIGHT = 48
COLOURS = {
    "k00001": (220, 40, 40),
    "k00002": (40, 180, 60),
    "k00003": (40, 80, 200),
    "k00004": (220, 200, 40),
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _role(path: str) -> str:
    if path.endswith(".bin"):
        return "features"
    if path.startswith("streams/") or "/streams/" in path:
        return "stream"
    return "keyframe"


def _round(value: float) -> float:
    return round(value, 4)


def _scale_intrinsics(values: list[float], sx: float, sy: float) -> list[float]:
    fx, fy, cx, cy = values
    return [_round(fx * sx), _round(fy * sy), _round(cx * sx), _round(cy * sy)]


def _scale_xy(values: list[float], sx: float, sy: float) -> list[float]:
    scaled: list[float] = []
    for index, value in enumerate(values):
        factor = sx if index % 2 == 0 else sy
        scaled.append(_round(float(value) * factor))
    return scaled


def _jpeg(colour: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (WIDTH, HEIGHT), colour)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90, subsampling=0, optimize=False)
    return buffer.getvalue()


def _features(count: int) -> bytes:
    size = 20 * count
    return bytes(index % 251 for index in range(size))


def _gzip_csv(columns: list[str]) -> bytes:
    text = ",".join(columns) + "\n" + ",".join(["0"] * len(columns)) + "\n"
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as handle:
        handle.write(text.encode())
    return buffer.getvalue()


def _resize_frame(frame: dict, scales: dict[str, tuple[float, float]]) -> None:
    sx = WIDTH / int(frame["w"])
    sy = HEIGHT / int(frame["h"])
    scales[frame["id"]] = (sx, sy)
    frame["w"] = WIDTH
    frame["h"] = HEIGHT
    if "intrinsics" in frame:
        frame["intrinsics"] = _scale_intrinsics(frame["intrinsics"], sx, sy)


def build(example: dict) -> tuple[dict, dict[str, bytes]]:
    packet = json.loads(json.dumps(example))
    packet["packetId"] = "mini-arkit"
    packet["source"]["kind"] = "synthetic"
    scales: dict[str, tuple[float, float]] = {}
    for frame in packet["keyframes"]:
        _resize_frame(frame, scales)
    for still in packet.get("stills") or []:
        sx = WIDTH / int(still["w"])
        sy = HEIGHT / int(still["h"])
        still["w"] = WIDTH
        still["h"] = HEIGHT
        if "intrinsics" in still:
            still["intrinsics"] = _scale_intrinsics(still["intrinsics"], sx, sy)
    for tap in packet.get("taps") or []:
        sx, sy = scales[tap["keyframe"]]
        if "pixel" in tap:
            tap["pixel"] = _scale_xy(tap["pixel"], sx, sy)
    door_views = ((packet.get("scaleReference") or {}).get("ext") or {}).get("doorViews") or []
    for view in door_views:
        sx, sy = scales[view["frame"]]
        if view.get("box") is not None:
            view["box"] = _scale_xy(view["box"], sx, sy)

    files: dict[str, bytes] = {}
    for frame in packet["keyframes"]:
        files[frame["img"]] = _jpeg(COLOURS[frame["id"]])
        features = frame.get("features")
        if features and features.get("file"):
            files[features["file"]] = _features(int(features["count"]))
    for still in packet.get("stills") or []:
        files.setdefault(still["img"], _jpeg(COLOURS.get(still.get("keyframe") or "", (80, 80, 80))))
    for stream in (packet.get("streams") or {}).values():
        files[stream["file"]] = _gzip_csv(stream["columns"])

    packet["files"] = [
        {
            "path": path,
            "bytes": len(data),
            "sha256": _sha256(data),
            "role": _role(path),
        }
        for path, data in sorted(files.items())
    ]
    return packet, files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--example", type=Path, default=Path("data/fixtures/packets/examples/example-packet.json"))
    parser.add_argument("--out", type=Path, default=Path("data/fixtures/packets/mini-arkit"))
    args = parser.parse_args(argv)
    example = json.loads(args.example.read_text())
    packet, files = build(example)
    if args.out.exists():
        for child in args.out.rglob("*"):
            if child.is_file():
                child.unlink()
    for path, data in files.items():
        dest = args.out / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    text = json.dumps(packet, indent=2) + "\n"
    (args.out / "packet.json").write_text(text)
    print(f"wrote {args.out / 'packet.json'} ({len(text)} bytes, {len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
