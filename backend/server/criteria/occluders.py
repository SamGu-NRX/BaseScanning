"""Laser and recon PLYs are the occluders a visibility score is allowed to use.

A missing cloud is an error. Scoring with no blockers leaves the z-buffer empty, so every
camera counts as seeing the wall and a site can pass on views it never had.
"""

from __future__ import annotations

import re
from pathlib import Path

_SURFACE_KINDS = {"point_cloud", "mesh"}


def ply_vertex_count(path: Path) -> int:
    """Vertex count from a PLY header. Zero when the file is missing, empty, or not a PLY."""
    try:
        with Path(path).open("rb") as handle:
            blob = b""
            while b"end_header" not in blob:
                line = handle.readline()
                if not line:
                    return 0
                blob += line
                if len(blob) > 1_000_000:
                    return 0
    except OSError:
        return 0
    match = re.search(rb"element vertex (\d+)", blob)
    if match is None:
        return 0
    return int(match.group(1))


def _is_ply(asset: dict) -> bool:
    path = str(asset.get("path") or "")
    fmt = str(asset.get("format") or "").lower()
    return fmt == "ply" or path.lower().endswith(".ply")


def missing_occluder_geometry(scene: dict, scene_dir: Path) -> bool:
    """True when a laser or recon PLY is absent, empty, or a declared occluders asset will not load."""
    scene_dir = Path(scene_dir)
    assets = list(scene.get("assets") or [])
    surface = [
        asset
        for asset in assets
        if asset.get("id") != "occluders" and asset.get("kind") in _SURFACE_KINDS and _is_ply(asset)
    ]
    if not surface:
        return True
    required = surface + [asset for asset in assets if asset.get("id") == "occluders"]
    for asset in required:
        path = scene_dir / str(asset.get("path") or "")
        if ply_vertex_count(path) <= 0:
            return True
    return False


def assert_not_provisional(report: dict) -> None:
    """Gates call this. A debug run that skipped occluders cannot pass."""
    if report.get("provisional"):
        raise ValueError("provisional")
