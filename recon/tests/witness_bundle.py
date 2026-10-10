"""Synthetic scan bundles for the coverage-extent witnesses, rendered here from analytic planes.

Nothing here is a capture of a real home: every surface is a plane, every LiDAR depth map is
rendered from those planes with exact poses plus fixed-seed sensor noise, and no private rule or
real-world coordinate enters a fixture. The renderer writes the app's bundle layout (scene.json,
keyframe JPEGs, Float32 depth and UInt8 confidence), so a witness can go through
`pipeline.run` - the caller a real bundle takes - with `--depth lidar`: no model process and no
network. Run standalone to write a bundle for a pipeline smoke:

    uv run python tests/witness_bundle.py /tmp/witness-smoke/bundle
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

FEET = 0.3048
W, H = 640, 480
K_DEFAULT = (400.0, 400.0, 320.0, 240.0)  # fx, fy, cx, cy in pixels of the 640 x 480 image
NOISE_M = 0.005  # fixed-seed depth noise, so surfaces are not perfectly planar


@dataclass(frozen=True)
class Surface:
    """A piece of the plane n . (p - p0) = 0, kept where the hit lies inside an axis-aligned box."""

    normal: np.ndarray
    point: np.ndarray
    lo: np.ndarray
    hi: np.ndarray


def wall(x0: float, x1: float, z: float = 0.0, y0: float = 0.0, y1: float = 2.2) -> Surface:
    """A vertical wall piece at depth `z`, facing +z: toward cameras standing at z > 0."""
    return Surface(
        np.array([0.0, 0.0, 1.0]),
        np.array([0.0, 0.0, z]),
        np.array([x0, y0, z - 0.05]),
        np.array([x1, y1, z + 0.05]),
    )


def back_wall(x0: float, x1: float, z: float = -1.5, y1: float = 2.2) -> Surface:
    """A wall behind the target wall, so a view through a hole measures something (not NaN)."""
    return wall(x0, x1, z=z, y1=y1)


def ground(fall: float = 0.0) -> Surface:
    """The floor: y = -fall * z, falling `fall` metres per metre going out from the wall (z)."""
    n = np.array([0.0, 1.0, fall]) / np.sqrt(1.0 + fall * fall)
    return Surface(n, np.zeros(3), np.full(3, -5.0), np.full(3, 5.0))


def render(surfaces: list[Surface], pos: np.ndarray, k: tuple[float, ...]) -> np.ndarray:
    """Depth in metres, ARKit camera axes (identity rotation looks along -z), NaN where no
    surface is in reach."""
    fx, fy, cx, cy = k
    u = np.arange(W) + 0.5
    v = np.arange(H) + 0.5
    xn = (u - cx) / fx
    yn = -(v - cy) / fy
    dirs = np.stack(np.broadcast_arrays(xn[None, :], yn[:, None], -np.ones((H, W))), axis=-1)
    depth = np.full((H, W), np.inf)
    for s in surfaces:
        t = ((s.point - pos) @ s.normal) / (dirs @ s.normal)
        pts = pos + t[..., None] * dirs
        hit = (t > 0.05) & np.all((pts >= s.lo) & (pts <= s.hi), axis=-1)
        depth = np.where(hit & (t < depth), t, depth)
    depth[~np.isfinite(depth)] = np.nan
    return depth


def write_frame(
    root: Path,
    fid: str,
    pos: np.ndarray,
    k: tuple[float, ...],
    surfaces: list[Surface],
    rng: np.random.Generator,
) -> dict:
    """One keyframe: JPEG, LiDAR depth and confidence files, and its scene.json entry (feet)."""
    depth = render(surfaces, pos, k) + rng.normal(0.0, NOISE_M, (H, W))
    depth.astype("<f4").tofile(root / f"{fid}.f32")
    np.where(np.isfinite(depth), 2, 0).astype(np.uint8).tofile(root / f"{fid}.u8")
    cv2.imwrite(str(root / f"{fid}.jpg"), np.full((H, W, 3), 128, np.uint8))
    pose = np.eye(4)
    pose[:3, 3] = pos
    pose[:3, 3] /= FEET  # scene.json translations are feet
    return {
        "id": fid,
        "pose": [round(float(x), 4) for x in pose.T.reshape(-1)],
        "intrinsics": [round(float(x), 4) for x in k],
        "w": W,
        "h": H,
        "img": f"{fid}.jpg",
        "depth": {"file": f"{fid}.f32", "confidenceFile": f"{fid}.u8", "w": W, "h": H},
    }


def scan_doc(kfs: list[dict], baseline_ft: list[tuple[float, float]], meter_ft: tuple) -> dict:
    """A bundle scene.json: the phone's marks (its wall baseline may claim more than it saw)."""
    return {
        "schema_version": "1.0",
        "meter": {"pos": [round(float(v), 4) for v in meter_ft], "wall_id": "wall"},
        "walls": [{"id": "wall", "baseline": [[x, z] for x, z in baseline_ft]}],
        "objects": [],
        "ground": [],
        "coverage": {"ends": {"left": {"kind": "unexplored"}, "right": {"kind": "unexplored"}}},
        "keyframes": kfs,
    }


def write_flat_room(
    root: Path,
    wall_x: tuple[float, float] | None = (0.1, 2.6),
    baseline_x: tuple[float, float] = (-0.6, 3.2),
    xs: tuple[float, ...] = (0.3, 0.75, 1.2, 1.65, 2.1),
    hole: tuple[float, float] | None = None,
    fall: float = 0.0,
    k: tuple[float, ...] = K_DEFAULT,
    behind: bool = True,
    meter_x: float = 1.0,
) -> Path:
    """A room of planes: one target wall along +x at z = 0 (two pieces around `hole`), optionally
    a wall behind it, and a floor falling `fall` per metre out. Cameras stand `xs` apart along
    the wall, 1 m up, 2 m out, looking along -z. The phone's baseline runs `baseline_x`, the
    meter `meter_x`, all in metres of x."""
    surfaces = [ground(fall)]
    root.mkdir(parents=True, exist_ok=True)
    if wall_x is not None:
        if hole is not None:
            surfaces += [wall(wall_x[0], hole[0]), wall(hole[1], wall_x[1])]
        else:
            surfaces += [wall(*wall_x)]
    if behind:
        span = (-1.0, 4.0) if wall_x is None else (wall_x[0] - 1.0, wall_x[1] + 1.0)
        surfaces += [back_wall(*span)]
    rng = np.random.default_rng(1234)
    kfs = [
        write_frame(root, f"k{i}", np.array([x, 1.0, 2.0]), k, surfaces, rng)
        for i, x in enumerate(xs)
    ]
    doc = scan_doc(
        kfs, [(baseline_x[0] / FEET, 0.0), (baseline_x[1] / FEET, 0.0)], (meter_x / FEET, 4.0, 0.1)
    )
    (root / "scene.json").write_text(json.dumps(doc, indent=1))
    return root


def main() -> None:
    ap = argparse.ArgumentParser(description="Write a synthetic bundle for the coverage witnesses")
    ap.add_argument("root", type=Path)
    args = ap.parse_args()
    root = write_flat_room(args.root)
    print(f"wrote synthetic bundle to {root}")


if __name__ == "__main__":
    main()
