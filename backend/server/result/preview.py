"""Preview cloud. Fixed-stride subsample, binary little-endian PLY, ``generated`` flag."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from server.result.harness import sceneio


def build_preview(scene: dict, scene_dir: Path, out_path: Path, *, max_points: int) -> str | None:
    """Write ``preview.ply``. Returns a warning code, or None on success."""

    existing = scene_dir / "preview.ply"
    if existing.is_file():
        cloud = sceneio.read_ply(existing)
        xyz = np.c_[cloud["x"], cloud["y"], cloud["z"]].astype(float)
        gen = cloud["generated"].astype(np.uint8) if "generated" in cloud else _flags(scene, xyz)
        xyz, gen = _stride(xyz, gen, max_points)
        sceneio.write_ply(out_path, xyz, {"generated": gen})
        return None

    xyz, gen_bool, _fields = sceneio.load_points(scene, scene_dir)
    if xyz is None:
        return "preview_skipped"
    gen = gen_bool.astype(np.uint8) if gen_bool is not None else _flags(scene, xyz)
    inside = _region_flags(scene, xyz)
    gen = np.where(inside, np.uint8(1), gen.astype(np.uint8))
    xyz, gen = _stride(xyz, gen, max_points)
    sceneio.write_ply(out_path, xyz, {"generated": gen})
    return None


def _stride(xyz: np.ndarray, gen: np.ndarray, max_points: int) -> tuple[np.ndarray, np.ndarray]:
    count = len(xyz)
    if count <= max_points:
        return xyz, gen.astype(np.uint8)
    stride = max(1, math.ceil(count / max_points))
    idx = np.arange(0, count, stride)
    return xyz[idx], gen.astype(np.uint8)[idx]


def _flags(scene: dict, xyz: np.ndarray) -> np.ndarray:
    return _region_flags(scene, xyz)


def _region_flags(scene: dict, xyz: np.ndarray) -> np.ndarray:
    out = np.zeros(len(xyz), dtype=np.uint8)
    for region in scene.get("generated_regions") or []:
        geom = region.get("geometry") or {}
        kind = geom.get("kind")
        if kind == "box" and geom.get("center_m") and geom.get("size_m"):
            out = np.where(_in_box(xyz, geom), np.uint8(1), out)
        elif kind == "footprint" and geom.get("polygon_m"):
            out = np.where(_in_footprint(xyz, geom), np.uint8(1), out)
    return out


def _in_box(xyz: np.ndarray, geom: dict) -> np.ndarray:
    center = np.asarray(geom["center_m"], float)
    size = np.asarray(geom["size_m"], float)
    yaw = np.radians(float(geom.get("yaw_deg") or 0.0))
    delta = xyz - center
    c, s = np.cos(-yaw), np.sin(-yaw)
    x = c * delta[:, 0] - s * delta[:, 2]
    z = s * delta[:, 0] + c * delta[:, 2]
    y = delta[:, 1]
    return (np.abs(x) <= size[0] / 2) & (np.abs(y) <= size[1] / 2) & (np.abs(z) <= size[2] / 2)


def _in_footprint(xyz: np.ndarray, geom: dict) -> np.ndarray:
    poly = np.asarray(geom["polygon_m"], float)
    y0 = float(geom.get("bottom_m", -1e9))
    y1 = float(geom.get("top_m", 1e9))
    inside = np.array([_pip(p[0], p[1], poly) for p in xyz[:, [0, 2]]])
    return inside & (xyz[:, 1] >= y0) & (xyz[:, 1] <= y1)


def _pip(x: float, z: float, poly: np.ndarray) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, zi = poly[i]
        xj, zj = poly[j]
        if ((zi > z) != (zj > z)) and (x < (xj - xi) * (z - zi) / (zj - zi + 1e-15) + xi):
            inside = not inside
        j = i
    return inside
