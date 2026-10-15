"""Write a 12 m flat wall with concrete ground, a meter, and synthetic cameras.

The scene is the positive-pass fixture: both ends are limits, the ground strip is
observed, and the point cloud sits just behind the wall face so face probes are
not self-occluded. Nothing here is an ETH3D asset.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def pose(x: float, y: float, z: float, pitch_deg: float = 0.0) -> list[float]:
    """Camera-to-world, column-major. pitch_deg > 0 looks down from a horizontal -z gaze."""
    pitch = np.radians(pitch_deg)
    cam_x = np.array([1.0, 0.0, 0.0])
    look = np.array([0.0, -np.sin(pitch), -np.cos(pitch)])
    cam_z = -look
    cam_y = np.cross(cam_z, cam_x)
    cam_y /= np.linalg.norm(cam_y)
    transform = np.eye(4)
    transform[:3, :3] = np.column_stack([cam_x, cam_y, cam_z])
    transform[:3, 3] = (x, y, z)
    return [float(v) for v in transform.flatten(order="F")]


def scene_dict() -> dict:
    length = 12.0
    half = length / 2
    bands = [{"wall_id": "w1", "band": "wall", "s_m": [-half, half], "views": 8, "best_incidence_deg": 5.0}]
    # Past the wall ends so a spot at the corner still has observed ground inside its clearance.
    ground = {
        "wall_id": "w1",
        "band": "ground",
        "s_m": [-half - 8.0, half + 8.0],
        "out_m": 8.0,
        "views": 8,
        "best_incidence_deg": 40.0,
    }
    overhead = {
        "wall_id": "w1",
        "band": "overhead",
        "s_m": [-half, half],
        "out_m": 1.0,
        "views": 8,
        "best_incidence_deg": 10.0,
    }
    sensitivity = []
    for rule, deg in (("angle75", 75.0), ("angle80", 80.0), ("footprint3in", 88.0)):
        sensitivity.append(
            {
                "surface": "ground",
                "rule_id": rule,
                "max_incidence_deg": deg,
                "bands": [dict(ground)],
            }
        )
    keyframes = []
    xs = np.linspace(-half + 0.2, half - 0.2, 8)
    # Eight head-on frames see the wall; sixteen downward frames see the ground strip.
    rows = ((1.6, 3.0, 8.0, 360.0), (1.5, 3.2, 55.0, 280.0), (1.6, 5.5, 60.0, 280.0))
    index = 0
    for y, z, pitch, focal in rows:
        for x in xs:
            keyframes.append(
                {
                    "id": f"k{index:02d}",
                    "pose": pose(float(x), y, z, pitch),
                    "intrinsics": [focal, focal, 320.0, 240.0],
                    "w": 640,
                    "h": 480,
                    "pose_source": "ground_truth",
                }
            )
            index += 1
    return {
        "format": "base-scene/v0",
        "scene_id": "synthetic-flatwall",
        "provenance": {
            "data": "synthetic",
            "site": "synthetic-flatwall",
            "producer": {"path": "server.criteria.tests", "version": "0"},
            "scale_source": "native_metric",
            "gravity_source": "ground_truth",
            "inputs": {"images_given": 24, "images_used": 24, "sensors_used": ["images", "intrinsics"]},
        },
        "frame": {
            "units": "m",
            "convention": "meter_anchored/v0",
            "anchor": {"element_id": "meter", "plus_minus_m": 0.05, "heading_plus_minus_deg": 0.1},
            "source_frame": "synthetic",
            "from_source": pose(0.0, 0.0, 0.0, 0.0),
        },
        "walls": [
            {
                "id": "w1",
                "role": "house",
                "baseline_m": [[-half, 0.0], [half, 0.0]],
                "height_m": 3.0,
                "s_range_m": [-half, half],
                "plus_minus_m": 0.02,
                "evidence": "observed",
                "observed_fraction": 1.0,
            }
        ],
        "chain": {
            "wall_ids": ["w1"],
            "ends": {
                "left": {"kind": "limit", "point_m": [-half, 0.0], "s_m": -half},
                "right": {"kind": "limit", "point_m": [half, 0.0], "s_m": half},
            },
        },
        "ground": {
            "surface": {"kind": "plane", "normal": [0.0, 1.0, 0.0], "offset_m": 0.0},
            "plus_minus_m": 0.02,
            "regions": [
                {
                    "id": "g1",
                    "material": "concrete",
                    "polygon_m": [[-half - 8.0, 0.0], [half + 8.0, 0.0], [half + 8.0, 8.0], [-half - 8.0, 8.0]],
                    "evidence": "observed",
                    "plus_minus_m": 0.02,
                    "source": {"kind": "annotation"},
                }
            ],
        },
        "objects": [
            {
                "id": "meter",
                "class": "meter",
                "label": "electric meter",
                "geometry": {"kind": "point", "pos_m": [0.0, 1.5, 0.0]},
                "wall_id": "w1",
                "confidence": 1.0,
                "plus_minus_m": 0.05,
                "source": {"kind": "annotation"},
                "evidence": "observed",
            }
        ],
        "coverage": {
            "method": "synthetic_wall_and_ground",
            "bands": bands[:1] + [ground, overhead],
            "sensitivity": sensitivity,
            "rule": {"min_views": 2, "max_incidence_deg": {"wall": 75, "ground": 80}},
            "grid": {
                "asset": "coverage",
                "voxel_m": 0.1,
                "origin_m": [-7.0, -0.4, -0.4],
                "shape": [140, 36, 70],
                "min_views": 1,
            },
        },
        "keyframes": keyframes,
        "assets": [
            {
                "id": "laser",
                "kind": "point_cloud",
                "path": "laser.ply",
                "format": "ply",
                "in_scene_frame": True,
                "fields": ["x", "y", "z"],
            },
            {"id": "coverage", "kind": "coverage_grid", "path": "coverage.npz", "format": "npz"},
        ],
    }


def _points() -> np.ndarray:
    xs = np.arange(-6.2, 6.21, 0.08)
    ys = np.arange(0.05, 3.01, 0.08)
    xx, yy = np.meshgrid(xs, ys)
    wall = np.column_stack([xx.ravel(), yy.ravel(), np.full(xx.size, -0.04)])
    return wall.astype(np.float64)


def _grid() -> tuple[np.ndarray, np.ndarray]:
    """y-up occupancy. origin [-7, -0.4, -0.4], voxel 0.1.

    iz 4 is the wall plane (z = 0). iy 4 is the ground (y = 0). The wall is a thick
    occupied column, the ground in front is occupied, and the volume in front is free
    so C2 does not see an unknown envelope.
    """
    nx, ny, nz = 140, 36, 70
    state = np.ones((nx, ny, nz), np.uint8)  # observed free, then paint surfaces
    views = np.full((nx, ny, nz), 4, np.uint8)
    # Wall thickness just behind and on the face, from the ground up to 3 m.
    state[:, 4:34, 1:5] = 2
    # Ground skin in front of the wall.
    state[:, 4, 5:68] = 2
    # Keep the volume above that ground free (already 1).
    return state, views


def write(dest: Path | None = None) -> Path:
    dest = Path(dest or HERE / "scene")
    dest.mkdir(parents=True, exist_ok=True)
    scene = scene_dict()
    # Import the harness the same way the engine does, after the package is importable.
    root = Path(__file__).resolve().parents[5]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from server.criteria.harness import load

    h = load()
    pts = _points()
    h.sceneio.write_ply(dest / "laser.ply", pts)
    state, views = _grid()
    np.savez_compressed(
        dest / "coverage.npz", state=state, origin=np.array([-7.0, -0.4, -0.4]), voxel=np.float64(0.1), views=views
    )
    text = json.dumps(scene, indent=1) + "\n"
    (dest / "scene.json").write_text(text)
    digest = hashlib.sha256((dest / "laser.ply").read_bytes()).hexdigest()
    scene["assets"][0]["sha256"] = digest
    scene["assets"][0]["count"] = int(len(pts))
    scene["assets"][1]["sha256"] = hashlib.sha256((dest / "coverage.npz").read_bytes()).hexdigest()
    (dest / "scene.json").write_text(json.dumps(scene, indent=1) + "\n")
    return dest


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    print(write(out))
