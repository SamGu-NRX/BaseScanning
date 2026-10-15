"""Cut a committed mini packet out of a packetgen tar. Deterministic ids, sorted files."""

from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
import uuid
from pathlib import Path

MINI_NOTE = "mini fixture: transport tests only, too few frames to reconstruct"
NAMESPACE = uuid.NAMESPACE_URL


def parse_range(text: str) -> tuple[str, str]:
    start, sep, end = text.partition(":")
    if not sep or not start or not end:
        raise ValueError(f"expected start:end, got {text}")
    return start, end


def minify(
    source: Path,
    variant: str,
    out: Path,
    *,
    keyframes: str | None = None,
    stills: str | None = None,
) -> dict:
    if (keyframes is None) == (stills is None):
        raise ValueError("pass exactly one of keyframes or stills")
    root, packet = _load(source, variant)
    house = _house_id(packet, source)
    if keyframes:
        start, end = parse_range(keyframes)
        label = keyframes
        kept_frames = [row for row in packet.get("keyframes") or [] if start <= row["id"] <= end]
        kept_ids = {row["id"] for row in kept_frames}
        kept_stills = [row for row in packet.get("stills") or [] if row.get("keyframe") in kept_ids]
        tier = packet["session"]["tier"]
        taps = [row for row in packet.get("taps") or [] if row.get("keyframe") in kept_ids]
        if tier != "photo_only" and not taps:
            raise ValueError(f"range {keyframes} has no tap")
    else:
        start, end = parse_range(stills or "")
        label = stills or ""
        kept_frames = []
        kept_ids = set()
        kept_stills = [row for row in packet.get("stills") or [] if start <= row["id"] <= end]
        taps = []
        if packet["session"]["tier"] != "photo_only":
            raise ValueError("--stills is for the photo_only variant")
    if not kept_frames and not kept_stills:
        raise ValueError("range kept no frames")

    mini = json.loads(json.dumps(packet))
    mini["keyframes"] = kept_frames
    mini["stills"] = kept_stills
    mini["taps"] = taps
    mini["packetId"] = str(uuid.uuid5(NAMESPACE, f"{house}/{variant}/mini/{label}"))
    notes = mini["source"].get("notes") or ""
    if MINI_NOTE not in notes:
        mini["source"]["notes"] = (notes + " " + MINI_NOTE).strip()
    mini["source"]["kind"] = "synthetic"
    _filter_doors(mini, kept_ids | {row["id"] for row in kept_stills})
    path_map = _rewrite_paths(mini, variant)
    out.mkdir(parents=True, exist_ok=True)
    _copy_files(root, out, path_map)
    mini["files"] = _files(out, path_map, packet.get("files") or [])
    (out / "packet.json").write_text(json.dumps(mini, indent=1) + "\n")
    return mini


def _house_id(packet: dict, source: Path) -> str:
    dataset = (packet.get("source") or {}).get("sourceDataset") or ""
    if "electro" in dataset:
        return "eth3d-electro"
    name = source.name
    if name.startswith("eth3d-"):
        return name.split(".")[0]
    return source.stem.split(".")[0]


def _load(source: Path, variant: str) -> tuple[Path, dict]:
    if source.is_file() and ".tar" in source.name:
        cache = Path(tempfile.gettempdir()) / "hss-ios-kit" / source.stem
        nested = cache / f"packet.{variant}.json"
        found = list(cache.glob(f"*/packet.{variant}.json"))
        if not nested.is_file() and not found:
            cache.mkdir(parents=True, exist_ok=True)
            with tarfile.open(source) as archive:
                archive.extractall(cache, filter="data")
            found = list(cache.glob(f"*/packet.{variant}.json"))
        root = nested.parent if nested.is_file() else found[0].parent
        return root, json.loads((root / f"packet.{variant}.json").read_text())
    if source.is_dir():
        path = source / f"packet.{variant}.json"
        if not path.is_file():
            path = source / "packet.json"
        return source, json.loads(path.read_text())
    raise ValueError(f"unrecognised source {source}")


def _filter_doors(packet: dict, keep: set[str]) -> None:
    scale = packet.get("scaleReference") or {}
    ext = scale.get("ext") or {}
    if isinstance(ext.get("doorViews"), list):
        ext["doorViews"] = [row for row in ext["doorViews"] if row.get("frame") in keep]


def _rewrite_paths(packet: dict, variant: str) -> dict[str, str]:
    """Map original relative path → spec layout path."""
    mapping: dict[str, str] = {}
    photo = packet["session"]["tier"] == "photo_only"
    for frame in packet.get("keyframes") or []:
        img = frame["img"]
        mapping[img] = img
        features = frame.get("features")
        if isinstance(features, dict) and features.get("file"):
            new = f"keyframes/{frame['id']}.features.bin"
            mapping[features["file"]] = new
            features["file"] = new
        depth = frame.get("depth")
        if isinstance(depth, dict):
            if depth.get("file"):
                mapping[depth["file"]] = depth["file"]
            if depth.get("confidenceFile"):
                mapping[depth["confidenceFile"]] = depth["confidenceFile"]
    for still in packet.get("stills") or []:
        img = still.get("img")
        if not img:
            continue
        if photo:
            new = f"stills/{still['id']}.jpg"
            mapping[img] = new
            still["img"] = new
        else:
            mapping[img] = img
    streams = packet.get("streams") or {}
    if isinstance(streams, dict):
        for name, spec in streams.items():
            if isinstance(spec, dict) and spec.get("file"):
                new = f"streams/{name}.csv.gz"
                mapping[spec["file"]] = new
                spec["file"] = new
    del variant
    return mapping


def _copy_files(root: Path, out: Path, mapping: dict[str, str]) -> None:
    for old, new in mapping.items():
        src = root / old
        dest = out / new
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())


def _files(out: Path, mapping: dict[str, str], original: list[dict]) -> list[dict]:
    roles = {row["path"]: row.get("role") for row in original}
    rows = []
    for old, new in sorted(mapping.items(), key=lambda item: item[1]):
        data = (out / new).read_bytes()
        rows.append(
            {
                "path": new,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "role": roles.get(old) or _role(new),
            }
        )
    rows.sort(key=lambda row: row["path"])
    return rows


def _role(path: str) -> str:
    if path.endswith(".features.bin"):
        return "features"
    if path.endswith(".depth.f32"):
        return "depth"
    if path.endswith(".confidence.u8"):
        return "confidence"
    if path.endswith(".csv.gz"):
        return "stream"
    if "/stills/" in path or path.startswith("stills/"):
        return "still"
    return "keyframe"
