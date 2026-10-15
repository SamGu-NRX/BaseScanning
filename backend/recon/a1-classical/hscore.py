"""Score one A1 facade run with the shared geometry scorer (evalharness score-cloud).

    python hscore.py <run> dense|sparse

The cloud is voxelized to 2 cm (the laser GT's resolution), cropped to 20 m from any input camera
(drops far background OpenMVS also reconstructs) and written as binary PLY; cameras are the run's
COLMAP model.
`--metric` (rigid alignment, scale error kept) for every mode that claims metres: gt and the ARKit
runs. The no-pose runs (incremental, global) are scale-free, so they get a similarity (shape only).
Output: internal/eval-harness/results/eth3d-facade/A1-colmap/<run>_<kind>/geomscore.{json,md}.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree

from common import STORE, WORK, read_text_model, umeyama

HARNESS = STORE / "internal/eval-harness"
GT = Path.home() / "a1/facade-gt/scene.json"
NO_SCALE = {"incremental", "global"}
VOXEL_M = 0.02  # the harness's laser GT is 2 cm voxels
CROP_CAMERAS_M = 20.0


def main(run: str, kind: str) -> None:
    d = WORK / "facade"
    _, gt_images = read_text_model(d / "gt_model")
    out = HARNESS / "results/eth3d-facade/A1-colmap" / f"{run}_{kind}"
    out.mkdir(parents=True, exist_ok=True)
    ply = d / "scores" / f"{run}_{kind}_1cm.ply"
    ply.parent.mkdir(exist_ok=True)
    if kind == "dense":
        pc = o3d.io.read_point_cloud(str(d / "mvs" / f"{run}_r1" / "scene_dense.ply"))
    else:
        pts = [ln.split()[1:4] for ln in (d / "sparse" / run / "points3D.txt").read_text().splitlines()
               if ln.strip() and not ln.startswith("#")]
        pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.array(pts, float)))
    pc = pc.voxel_down_sample(VOXEL_M)
    pc.colors = o3d.utility.Vector3dVector()
    P = np.asarray(pc.points)
    _, images = read_text_model(d / "sparse" / run)
    C = np.array([im["center"] for im in images.values()])
    # Crop to CROP_CAMERAS_M from any camera, in the run's own units (no-pose runs: scaled by the
    # SfM-to-GT similarity scale, so the crop radius is the same in metres for every mode).
    s = umeyama(C, np.array([gt_images[n]["center"] for n in images]), True)[0] if run in NO_SCALE else 1.0
    dist, _ = cKDTree(C).query(P, workers=4)
    pc = pc.select_by_index(np.flatnonzero(dist * s < CROP_CAMERAS_M))
    o3d.io.write_point_cloud(str(ply), pc, write_ascii=False)
    cmd = [sys.executable, "-m", "evalharness.cli", "score-cloud", "--gt", str(GT), "--cloud", str(ply),
           "--cameras", str(d / "sparse" / run), "--producer", f"A1-colmap-{kind}", "--out", str(out)]
    if run not in NO_SCALE:
        cmd.append("--metric")
    subprocess.run(cmd, check=True, cwd=HARNESS)
    big = out / "points.ply"
    if big.exists() and big.stat().st_size > 20e6:
        vm = d / "scores" / f"{run}_{kind}_scored_points.ply"
        shutil.move(big, vm)
        (out / "points.ply.MOVED.txt").write_text(f"Too large for the store; moved to the A1 VM: {vm}\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
