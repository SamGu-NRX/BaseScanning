"""Openings median and p90 for one A6 segment, on the exported sparse model.

Uses the A6 laser lookup (`recon/a6-iphone-quality/laser.py`) and the
alignment-free pair scorer. The gate number is the patch-averaged p90 in the
0.5–2 m bin, for points within 10 m of the camera. NavVis depth PNGs must
already be under ``$HSS_LAMAR_ROOT/colmap/LIN/navvis_depth/``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pycolmap

HERE = Path(__file__).resolve().parents[1]
A6 = HERE / "recon" / "a6-iphone-quality"
OPENINGS = "0.5-2m (openings)"
FAR = "4.57-7.92m (15-26 ft)"


def _laser():
    root = os.environ.get("HSS_LAMAR_ROOT")
    if not root:
        raise SystemExit("HSS_LAMAR_ROOT is not set")
    source = (A6 / "laser.py").read_text()
    source = source.replace('ROOT = os.path.expanduser("~/lamar")', "ROOT = os.environ['HSS_LAMAR_ROOT']")
    source = source.replace(
        '_, w, h, fx, fy, cx, cy = g["cam"]',
        'cam = g["cam"]\n'
        "        if isinstance(cam, dict):\n"
        '            w, h, fx, fy, cx, cy = cam["width"], cam["height"], *cam["params"]\n'
        "        else:\n"
        "            _, w, h, fx, fy, cx, cy = cam",
    )
    namespace = {"__file__": str(A6 / "laser.py"), "__name__": "laser"}
    exec(compile(source, str(A6 / "laser.py"), "exec"), namespace)
    return namespace


def _pairs(rec: pycolmap.Reconstruction, seg: dict, gt: dict, laser: dict, cloud: np.ndarray):
    by_file = {view.split("/")[1]: view for view in seg["gt_views"]}
    depth_cache: dict[str, np.ndarray] = {}
    points, laser_pts, ranges = [], [], []
    for point in rec.points3D.values():
        for element in point.track.elements:
            image = rec.images[element.image_id]
            view = by_file.get(image.name)
            if view is None:
                continue
            record = gt[view]
            cam = record["cam"]
            if isinstance(cam, dict):
                width, height = cam["width"], cam["height"]
                fx, fy, cx, cy = cam["params"]
            else:
                _, width, height, fx, fy, cx, cy = cam
            if image.name not in depth_cache:
                depth_cache[image.name] = laser["render_depth"](
                    cloud, record["R"], record["t"], [fx, fy, cx, cy], width, height
                )
            uv = np.asarray([image.points2D[element.point2D_idx].xy], float)
            depth = laser["lookup"](depth_cache[image.name], uv)[0]
            if np.isfinite(depth) and depth < 20:
                twin = laser["backproject"](uv, np.array([depth]), record["R"], record["t"], [fx, fy, cx, cy])[0]
                centre = -(np.asarray(record["R"], float).T @ np.asarray(record["t"], float))
                points.append(point.xyz)
                laser_pts.append(twin)
                ranges.append(float(np.linalg.norm(twin - centre)))
            break
    return np.asarray(points, float), np.asarray(laser_pts, float), np.asarray(ranges, float)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score LaMAR openings on an exported sparse model")
    parser.add_argument("--segment", required=True)
    parser.add_argument("--poses", type=Path, required=True, help="Directory with sparse/0")
    parser.add_argument("--range-m", type=float, default=10.0)
    args = parser.parse_args(argv)
    root = Path(os.environ["HSS_LAMAR_ROOT"])
    sys.path.insert(0, str(A6))
    from pairscore import score

    laser = _laser()
    segments = json.loads((A6 / "results" / "segments.json").read_text())
    seg = next(item for item in segments if item["id"] == args.segment)
    gt = json.loads((root / "gt_ios.json").read_text())
    query = np.stack(
        [-(np.asarray(gt[view]["R"], float).T @ np.asarray(gt[view]["t"], float)) for view in seg["gt_views"]]
    )
    cache = root / "laser" / f"{seg['id']}.npz"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cloud = laser["build_cloud"](query, radius=15.0, voxel=0.02, step=5, cache=str(cache))
    rec = pycolmap.Reconstruction(args.poses / "sparse" / "0")
    points, twins, ranges = _pairs(rec, seg, gt, laser, cloud)
    keep = ranges <= args.range_m
    scored = score(points[keep], twins[keep])
    openings = scored.get("patch " + OPENINGS) or {}
    far = scored.get("patch " + FAR) or {}
    points_bin = scored.get(OPENINGS) or {}
    summary = {
        "segment": seg["id"],
        "registered": rec.num_reg_images(),
        "points_scored": int(keep.sum()) if len(ranges) else 0,
        "openings_med_in": openings.get("med_in"),
        "openings_p90_in": openings.get("p90_in"),
        "openings_points_med_in": points_bin.get("med_in"),
        "openings_points_p90_in": points_bin.get("p90_in"),
        "far_med_in": far.get("med_in"),
        "far_p90_in": far.get("p90_in"),
        "outlier_frac": scored.get("outlier_frac"),
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
