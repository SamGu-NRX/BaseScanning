"""Copy a packet and drop location, service address, GPS, and named frames.

Pixels are not edited. JPEG GPS tags are removed with exiftool when it is
installed. If a JPEG still has a GPS IFD afterwards, the command exits 4.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image
from redact import redact


def scrub(packet_dir: Path, out_dir: Path, drop_frames: list[str]) -> dict:
    if not (packet_dir / "packet.json").is_file():
        raise FileNotFoundError(packet_dir / "packet.json")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(packet_dir, out_dir)
    packet_path = out_dir / "packet.json"
    packet = json.loads(packet_path.read_text())
    dropped = _drop_frames(packet, out_dir, drop_frames)
    removed = _strip_location(packet)
    packet = redact(packet)
    _rehash(packet, out_dir)
    packet_path.write_text(json.dumps(packet, indent=2) + "\n")
    gps_left = _strip_exif_gps(out_dir)
    if gps_left or _json_has_location(packet):
        raise PermissionError("GPS or location survived scrub: " + ", ".join(gps_left))
    return {"dropped": dropped, "removed_fields": removed}


def _drop_frames(packet: dict, out_dir: Path, drop_frames: list[str]) -> list[str]:
    drop = {frame_id for frame_id in drop_frames if frame_id}
    if not drop:
        return []
    packet["keyframes"] = [item for item in packet.get("keyframes", []) if item.get("id") not in drop]
    packet["stills"] = [item for item in packet.get("stills", []) if item.get("id") not in drop]
    packet["taps"] = [item for item in packet.get("taps", []) if item.get("id") not in drop]
    kept_files = []
    for entry in packet.get("files", []):
        path = str(entry.get("path", ""))
        if any(_path_names_frame(path, frame_id) for frame_id in drop):
            target = out_dir / path
            if target.is_file():
                target.unlink()
            continue
        kept_files.append(entry)
    packet["files"] = kept_files
    return sorted(drop)


def _path_names_frame(path: str, frame_id: str) -> bool:
    name = Path(path).name
    return name == frame_id or name.startswith(frame_id + ".")


def _strip_location(packet: dict) -> list[str]:
    removed: list[str] = []
    streams = packet.get("streams")
    if isinstance(streams, dict) and "location" in streams:
        del streams["location"]
        removed.append("streams.location")
    for still in packet.get("stills") or []:
        if isinstance(still, dict) and "location" in still:
            del still["location"]
            removed.append(f"stills.{still.get('id', '?')}.location")
    scale = packet.get("scaleReference")
    if isinstance(scale, dict):
        ext = scale.get("ext")
        if isinstance(ext, dict) and "serviceAddress" in ext:
            del ext["serviceAddress"]
            removed.append("scaleReference.ext.serviceAddress")
    return removed


def _json_has_location(packet: dict) -> bool:
    streams = packet.get("streams")
    if isinstance(streams, dict) and "location" in streams:
        return True
    for still in packet.get("stills") or []:
        if isinstance(still, dict) and still.get("location"):
            return True
    scale = packet.get("scaleReference")
    if isinstance(scale, dict) and isinstance(scale.get("ext"), dict) and "serviceAddress" in scale["ext"]:
        return True
    return False


def _rehash(packet: dict, out_dir: Path) -> None:
    refreshed = []
    for entry in packet.get("files", []):
        path = out_dir / str(entry["path"])
        if not path.is_file():
            continue
        data = path.read_bytes()
        entry = dict(entry)
        entry["bytes"] = len(data)
        entry["sha256"] = hashlib.sha256(data).hexdigest()
        refreshed.append(entry)
    packet["files"] = refreshed


def _jpeg_has_gps(path: Path) -> bool:
    with Image.open(path) as image:
        exif = image.getexif()
        getter = getattr(exif, "get_ifd", None)
        if getter is None:
            return False
        return bool(getter(0x8825))


def _strip_exif_gps(out_dir: Path) -> list[str]:
    jpegs = [path for path in out_dir.rglob("*") if path.suffix.lower() in {".jpg", ".jpeg"}]
    if not jpegs:
        return []
    exiftool = shutil.which("exiftool")
    if exiftool is not None:
        subprocess.run([exiftool, "-gps:all=", "-overwrite_original", *[str(path) for path in jpegs]], check=False)
    return [str(path) for path in jpegs if _jpeg_has_gps(path)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrub a capture packet")
    parser.add_argument("--in", dest="src", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--drop", default="", help="Comma-separated keyframe or still ids")
    args = parser.parse_args(argv)
    drop = [part for part in args.drop.split(",") if part]
    try:
        report = scrub(args.src, args.out, drop)
    except FileNotFoundError as exc:
        print(f"missing input: {exc}", file=sys.stderr)
        return 3
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
