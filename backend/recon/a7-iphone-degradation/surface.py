"""Extraction-free surface metric for every scored A7 run: recon points vs the laser, near the meter.

    python surface.py            # fills "surface" / "surface_gtpose" in results/*/*/*.json

Uses the scorer's own aligned cloud (score/points.ply, GT scene frame) and the GT laser (scene frame).
Region: within 8 m (plan) of the meter, 0.1-3 m above the GT ground (y=0 at the meter), within 1.5 m of the
laser. accuracy = recon -> laser distance on points within 0.3 m of the laser (median / p90, inches);\noff_surface_share = share of points 0.3-1.5 m off the laser (depth through window glass, phantoms);
completeness = share of laser samples in the region with a recon point within 5 cm.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from plyfile import PlyData
from scipy.spatial import cKDTree

R = Path(__file__).parent / "results"
M_TO_IN = 39.3700787
_LASER: dict = {}


def ply_xyz(p: Path) -> np.ndarray:
    v = PlyData.read(str(p))["vertex"]
    return np.c_[v["x"], v["y"], v["z"]].astype(np.float64)


def region(P: np.ndarray) -> np.ndarray:
    return (np.hypot(P[:, 0], P[:, 2]) < 8.0) & (P[:, 1] > 0.1) & (P[:, 1] < 3.0)


def laser(scene: str):
    if scene not in _LASER:
        L = ply_xyz(Path.home() / f"a7/gt/{scene}-gt/laser.ply")
        L = L[region(L)]
        _LASER[scene] = (L, cKDTree(L))
    return _LASER[scene]


def icp_to_laser(P: np.ndarray, L: np.ndarray) -> np.ndarray:
    """Rigid point-to-plane ICP, coarse to fine: the scorer's cloud is in the recon's own meter frame
    (off by its anchor error), and this metric is about surface shape, not placement."""
    import open3d as o3d  # noqa: PLC0415  (heavy import, only needed here)

    src = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(P)).voxel_down_sample(0.03)
    dst = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(L))
    dst.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=0.1, max_nn=30))
    T = np.eye(4)
    for dist in (1.0, 0.4, 0.15, 0.06):
        T = o3d.pipelines.registration.registration_icp(
            src, dst, dist, T, o3d.pipelines.registration.TransformationEstimationPointToPlane(),
            o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=40)).transformation
    return P @ T[:3, :3].T + T[:3, 3]


def metric(scene: str, pts: Path, transform: Path | None = None) -> dict | None:
    """pts: the scorer's points.ply (scene frame), or a raw cloud plus its cloud->GT-frame transform."""
    if not pts.exists():
        return None
    L, tl = laser(scene)
    P = ply_xyz(pts)
    if transform is not None:
        T = np.array(json.loads(transform.read_text()))
        P = P @ T[:3, :3].T + T[:3, 3]
    P = P[np.hypot(P[:, 0], P[:, 2]) < 12.0]
    if len(P) < 50:
        return {"points": int(len(P))}
    P = icp_to_laser(P, L)
    P = P[region(P)]
    if len(P) < 50:
        return {"points": int(len(P))}
    d, _ = tl.query(P, workers=4)
    near = d < 1.5
    on = d < 0.3
    dc, _ = cKDTree(P[on]).query(L, workers=4)
    return {"points": int(near.sum()), "acc_med_in": float(np.median(d[on]) * M_TO_IN), "acc_p90_in": float(np.percentile(d[on], 90) * M_TO_IN),
            "within_2in": float((d[near] < 0.0508).mean()), "off_surface_share": float((near & ~on).sum() / near.sum()),
            "compl_5cm": float((dc < 0.05).mean()), "icp": True}


def main() -> None:
    for f in sorted(R.glob("*/*/*.json")):
        if f.name == "summary.json":
            continue
        scene, path, cond = f.parent.parent.name, f.parent.name, f.stem
        rep = json.loads(f.read_text())
        base = Path.home() / "a7/scores" / scene / path / cond
        todo = [("surface", base / "score/points.ply")] if path == "A1" else \
            [("surface", base / "cloud/score/points.ply"), ("surface_gtpose", base / "cloud_gtpose/score/points.ply")]
        changed = False
        for key, pts in todo:
            if key in rep and rep[key] and rep[key].get("icp"):
                continue
            if pts.exists():
                rep[key] = metric(scene, pts)
            elif (pts.parent.parent / "transform.json").exists():
                rep[key] = metric(scene, pts.parent.parent / "cloud.ply", transform=pts.parent.parent / "transform.json")
            else:
                continue
            changed = True
        if changed:
            f.write_text(json.dumps(rep, indent=1, default=float))
            print(f, {k: rep.get(k) for k, _ in todo}, flush=True)


if __name__ == "__main__":
    main()
