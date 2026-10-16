"""Build the occupancy grid for a labelled scene from ETH3D data.

    python -m c2vol.build SCENE.json DATA_ROOT/<scene> OUT.npz --mode laser|photos [--cams all|keyframes]
        [--res 0.05] [--stride 4] [--max-angle 75]

laser:  rays from each scanner origin to every scan point (one per 5 cm voxel). Observed space is what
        the laser saw: the best case for any capture.
photos: rays from each DSLR camera centre to the nearest scan surface in each (downsampled) pixel,
        dropping surfaces seen more obliquely than --max-angle. Observed space is what those photos
        could have seen, with perfect geometry: the evidence a photo reconstruction can claim at best.
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
from matplotlib.path import Path as Path_
from scipy.spatial import cKDTree

from c2vol.eth3d import load_scans, read_cameras
from c2vol.grid import OCCUPIED, UNKNOWN, FREE, carve, depth_rays, unique_endpoints


def normals_pca(P: np.ndarray, k: int = 16) -> np.ndarray:
    tree = cKDTree(P)
    _, idx = tree.query(P, k=k, workers=-1)
    Q = P[idx] - P[idx].mean(1, keepdims=True)
    C = np.einsum("nki,nkj->nij", Q, Q)
    _, v = np.linalg.eigh(C)
    return v[:, :, 0]


OPENING_TYPES = ("window", "door", "garage_door", "window_well")


def opening_points(sc: dict, res: float) -> np.ndarray:
    """Points every `res` on each labelled opening's vertical mid-plane: the polygon's longest edge,
    moved to the polygon centroid, times its z_range. Glass and open doorways give no laser return,
    so these stand in for the surface the label says is there."""
    out = []
    for o in sc.get("objects", []):
        if o["type"] not in OPENING_TYPES:
            continue
        Q = np.array(o["plan_polygon"], float)
        E = np.roll(Q, -1, axis=0) - Q
        k = int(np.argmax(np.linalg.norm(E, axis=1)))
        a, d = Q[k], E[k]
        c = Q.mean(0)
        n = np.array([-d[1], d[0]]) / np.linalg.norm(d)
        a = a + n * np.dot(c - a, n)
        L = np.linalg.norm(d)
        s = np.arange(res / 2, L, res) / L
        z = np.arange(o["z_range"][0] + res / 2, o["z_range"][1], res)
        S, Z = np.meshgrid(s, z, indexing="ij")
        XY = a + S.reshape(-1, 1) * d
        out.append(np.column_stack([XY, Z.reshape(-1)]))
    return np.concatenate(out) if out else np.zeros((0, 3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene", type=Path)
    ap.add_argument("data", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--mode", choices=["laser", "photos"], default="laser")
    ap.add_argument("--cams", default="all")
    ap.add_argument("--res", type=float, default=0.05)
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--max-angle", type=float, default=75.0)
    ap.add_argument("--margin", type=float, default=25.0, help="plan margin of scan kept for rays")
    ap.add_argument("--no-fill-openings", dest="fill_openings", action="store_false",
                    help="do not add labelled openings (glass) as surfaces the sensor saw")
    a = ap.parse_args()
    sc = json.loads(a.scene.read_text())
    lo = np.array(sc["roi"]["min"], float)
    hi = np.array(sc["roi"]["max"], float)
    t0 = time.time()
    m = a.margin
    scans = load_scans(a.data / "dslr_scan_eval", ((lo[0] - m, lo[1] - m), (hi[0] + m, hi[1] + m)),
                       (lo[2] - 5, hi[2] + 25))
    info = {"mode": a.mode, "res": a.res, "roi": sc["roi"], "fill_openings": a.fill_openings}
    O = opening_points(sc, a.res) if a.fill_openings else np.zeros((0, 3))
    info["opening_points"] = len(O)
    extra = []
    if a.mode == "laser":
        rays = [(s.origin, unique_endpoints(s.points, a.res)) for s in scans]
        info["sensors"] = [{"name": s.name, "origin": s.origin.tolist(), "rays": len(r[1])}
                           for s, r in zip(scans, rays)]
        if len(O):
            extra = [(s.origin, O) for s in scans]
    else:
        P = np.concatenate([s.points for s in scans] + ([O.astype(np.float32)] if len(O) else []))
        P = unique_endpoints(P, a.res)
        # photos only see surfaces near the region; keep a generous box for occluders
        near = np.all((P[:, :2] > lo[:2] - 15) & (P[:, :2] < hi[:2] + 15), axis=1)
        P = P[near]
        N = normals_pca(P) if a.max_angle < 90 else None
        cams = read_cameras(a.data / "dslr_calibration_undistorted")
        names = sorted(cams) if a.cams == "all" else (
            sc["keyframes"] if a.cams == "keyframes" else a.cams.split(","))
        rays = [depth_rays(cams[n], P, a.stride, a.max_angle if N is not None else None, N)
                for n in names]
        info["sensors"] = [{"name": n, "origin": r[0].tolist(), "rays": len(r[1])}
                           for n, r in zip(names, rays)]
        info["stride"] = a.stride
        info["max_angle_deg"] = a.max_angle
    t1 = time.time()
    g = carve(lo, hi, a.res, rays, extra)
    # synthetic test objects: occupied voxels only (they cast no ray shadow, so the space behind
    # them keeps the real scan's observed state)
    info["synthetic"] = []
    for o in sc.get("objects", []):
        if not o.get("synthetic"):
            continue
        poly = Path_(o["plan_polygon"])
        k0 = int(np.floor((o["z_range"][0] - lo[2]) / a.res))
        k1 = int(np.ceil((o["z_range"][1] - lo[2]) / a.res))
        mn, mx = np.min(o["plan_polygon"], 0), np.max(o["plan_polygon"], 0)
        i0, j0 = np.floor((mn - lo[:2]) / a.res).astype(int)
        i1, j1 = np.ceil((mx - lo[:2]) / a.res).astype(int)
        I, J = np.meshgrid(np.arange(i0, i1), np.arange(j0, j1), indexing="ij")
        C = lo[:2] + (np.stack([I, J], -1) + 0.5) * a.res
        inside = poly.contains_points(C.reshape(-1, 2)).reshape(I.shape)
        n = 0
        for i, j in zip(I[inside], J[inside]):
            g.state[i, j, max(k0, 0):min(k1, g.shape[2])] = OCCUPIED
            n += 1
        info["synthetic"].append({"id": o["id"], "columns": n})
    t2 = time.time()
    info["timing_s"] = {"load_and_rays": round(t1 - t0, 1), "carve": round(t2 - t1, 1)}
    info["counts"] = {k: int((g.state == v).sum()) for k, v in
                      (("unknown", UNKNOWN), ("free", FREE), ("occupied", OCCUPIED))}
    info["shape"] = list(g.shape)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    g.save(a.out)
    a.out.with_suffix(".json").write_text(json.dumps(info, indent=1))
    print(json.dumps({k: info[k] for k in ("mode", "shape", "counts", "timing_s")}))


if __name__ == "__main__":
    main()
