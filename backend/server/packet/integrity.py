"""File presence, size, hash and path safety."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from server.packet.report import Bag, pointer


def resolve_under(root: Path, rel: str) -> Path | None:
    """Return the resolved path, or None when it escapes `root`."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        return None
    parts = Path(rel).parts
    if ".." in parts:
        return None
    full = (root / rel).resolve()
    try:
        full.relative_to(root.resolve())
    except ValueError:
        return None
    return full


def referenced_paths(pk: dict) -> list[tuple[str, str]]:
    """(json pointer, relative path) for every path a record names."""
    found: list[tuple[str, str]] = []
    for i, frame in enumerate(pk.get("keyframes") or []):
        found.append((pointer("keyframes", i, "img"), frame.get("img")))
        depth = frame.get("depth") or {}
        if isinstance(depth, dict):
            if depth.get("file"):
                found.append((pointer("keyframes", i, "depth", "file"), depth["file"]))
            if depth.get("confidenceFile"):
                found.append((pointer("keyframes", i, "depth", "confidenceFile"), depth["confidenceFile"]))
        features = frame.get("features") or {}
        if isinstance(features, dict) and features.get("file"):
            found.append((pointer("keyframes", i, "features", "file"), features["file"]))
    for i, still in enumerate(pk.get("stills") or []):
        found.append((pointer("stills", i, "img"), still.get("img")))
    streams = pk.get("streams") or {}
    if isinstance(streams, dict):
        for name, stream in streams.items():
            if isinstance(stream, dict) and stream.get("file"):
                found.append((pointer("streams", name, "file"), stream["file"]))
    mesh = pk.get("mesh") or {}
    if isinstance(mesh, dict) and mesh.get("file"):
        found.append((pointer("mesh", "file"), mesh["file"]))
    scale = pk.get("scaleReference") or {}
    ext = scale.get("ext") if isinstance(scale, dict) else None
    scan = ext.get("trueDepthScan") if isinstance(ext, dict) else None
    if isinstance(scan, dict):
        for i, fr in enumerate(scan.get("frames") or []):
            if not isinstance(fr, dict):
                continue
            if fr.get("depth"):
                found.append((pointer("scaleReference", "ext", "trueDepthScan", "frames", i, "depth"), fr["depth"]))
            if fr.get("image"):
                found.append((pointer("scaleReference", "ext", "trueDepthScan", "frames", i, "image"), fr["image"]))
    return [(ptr, rel) for ptr, rel in found if isinstance(rel, str)]


def check_files(
    pk: dict,
    root: Path,
    bag: Bag,
    *,
    check_hashes: bool,
    known_sha256: Mapping[str, str] | None,
) -> int:
    """Return how many files were hashed."""
    files_raw = pk.get("files")
    files = files_raw if isinstance(files_raw, list) else []
    seen: set[str] = set()
    listed: dict[str, dict] = {}
    hashed = 0
    for i, entry in enumerate(files):
        if not isinstance(entry, dict):
            bag.error("schema", pointer("files", i), "file entry must be an object")
            continue
        rel = entry.get("path")
        ptr = pointer("files", i, "path")
        if not isinstance(rel, str):
            bag.error("path_unsafe", ptr, "file path is not a string")
            continue
        if rel in seen:
            bag.error("file_duplicate", pointer("files", i, "path"), f"duplicate path {rel}")
        seen.add(rel)
        full = resolve_under(root, rel)
        if full is None:
            bag.error("path_unsafe", ptr, "path escapes the packet root")
            continue
        listed[rel] = entry
        if not full.is_file():
            bag.error("file_missing", pointer("files", i, "path"), f"missing {rel}")
            continue
        size = full.stat().st_size
        if size != entry.get("bytes"):
            bag.error(
                "file_size_mismatch",
                pointer("files", i, "bytes"),
                f"{rel} is {size} bytes, manifest says {entry.get('bytes')}",
                value=size,
            )
            continue
        expected = entry.get("sha256")
        if known_sha256 is not None and known_sha256.get(rel) == expected:
            continue
        if check_hashes:
            digest = hashlib.sha256(full.read_bytes()).hexdigest()
            hashed += 1
            if digest != expected:
                bag.error("sha256_mismatch", pointer("files", i, "sha256"), f"{rel} sha256 mismatch")
    for ptr, rel in referenced_paths(pk):
        if rel not in listed:
            bag.error("file_not_listed", ptr, f"{rel} is not listed in files")
    return hashed
