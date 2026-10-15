"""Derive a gravel copy of an eth3d-facade scene. Never writes ETH3D bytes into git.

The laser PLY stays beside the source scene. This module only rewrites scene.json
so a footprint3in run can treat the front-yard regions as gravel.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path


def _front_strip(wall: dict, depth_m: float = 6.0) -> list[list[float]]:
    """Polygon 0–depth_m in front of the wall. Outward matches the 3D engine."""
    (x0, z0), (x1, z1) = wall["baseline_m"]
    dx, dz = float(x1) - float(x0), float(z1) - float(z0)
    length = math.hypot(dx, dz) or 1.0
    ox, oz = -dz / length, dx / length
    return [
        [float(x0), float(z0)],
        [float(x1), float(z1)],
        [float(x1) + ox * depth_m, float(z1) + oz * depth_m],
        [float(x0) + ox * depth_m, float(z0) + oz * depth_m],
    ]


def write(src: Path, dest: Path) -> Path:
    src = Path(src)
    scene_path = src / "scene.json" if src.is_dir() else src
    if not scene_path.is_file():
        raise FileNotFoundError(scene_path)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    scene = json.loads(scene_path.read_text())
    ground = scene.setdefault("ground", {})
    regions = ground.setdefault("regions", [])
    for region in regions:
        if region.get("material") not in {"drive", "unknown"}:
            region["material"] = "gravel"
    if not any(region.get("id") == "g1" for region in regions):
        wall = next((w for w in scene.get("walls") or [] if w.get("id") == "w1"), None)
        if wall is None and scene.get("walls"):
            wall = scene["walls"][0]
        if wall is not None:
            regions.append(
                {
                    "id": "g1",
                    "material": "gravel",
                    "polygon_m": _front_strip(wall, 6.0),
                    "evidence": "observed",
                    "source": {"kind": "annotation"},
                }
            )
    (dest / "scene.json").write_text(json.dumps(scene, indent=1) + "\n")
    for name in ("coverage.npz",):
        origin = scene_path.parent / name
        target = dest / name
        if origin.is_file() and not target.exists():
            shutil.copy2(origin, target)
    for name in ("laser.ply", "occluders.ply"):
        origin = scene_path.parent / name
        target = dest / name
        if origin.exists() and not target.exists():
            target.symlink_to(origin.resolve())
    return dest


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: facade_gravel.py SRC DEST")
    print(write(Path(sys.argv[1]), Path(sys.argv[2])))
