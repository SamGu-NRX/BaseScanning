"""C2 inputs from a photo-built model: occupancy grid carved from the reconstruction's own rays,
and the C2 labelled scene rescaled about the meter by the same scale error as the cloud.

The E2E scene cloud and cameras (meter frame) go back to the ETH3D scan frame through the GT
scene's frame.from_source. Rays: each recon camera z-buffers the recon cloud at 1/stride
resolution (c2vol.grid.depth_rays, the same routine as C2's photo mode, but on reconstructed
instead of laser geometry), dropping surfaces seen past 75 deg. Labelled openings (glass) are
added as occlusion-stopped extra rays from every camera that has them in view; synthetic fixtures
are inserted as occupied voxels, as in c2vol.build.

    python c2_recon.py --c2scene C2.json --gt GT/scene.json --e2e E2E_DIR --out OUT_DIR
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

import numpy as np
from matplotlib.path import Path as Path_

C2 = Path.home() / "e2e/store/internal/breadth/c2-volumetric"
sys.path.insert(0, str(C2))
sys.path.insert(0, str(Path.home() / "e2e/store/internal/eval-harness"))
from c2vol.build import normals_pca, opening_points  # noqa: E402
from c2vol.eth3d import Camera  # noqa: E402
from c2vol.grid import OCCUPIED, UNKNOWN, FREE, carve, depth_rays, unique_endpoints  # noqa: E402
from evalharness.frame import mat4_from_list  # noqa: E402
from evalharness.sceneio import read_ply  # noqa: E402

FT = 0.3048


def scale_c2(sc: dict, e: float) -> dict:
    sc = copy.deepcopy(sc)
    m = np.array(sc["meter"]["pos"], float)
    f = 1 + e

    def p2(q):
        q = np.asarray(q, float)
        return (m[: len(q)] + f * (q - m[: len(q)])).round(4).tolist()

    def z(v):
        return round(float(m[2] + f * (v - m[2])), 4)

    for o in sc.get("objects", []):
        o["plan_polygon"] = [p2(q) for q in o["plan_polygon"]]
        o["z_range"] = [z(v) for v in o["z_range"]]
    for g in sc.get("ground_types", []):
        g["plan_polygon"] = [p2(q) for q in g["plan_polygon"]]
    if "ground_z_hint" in sc:
        sc["ground_z_hint"] = z(sc["ground_z_hint"])
    ch = sc.get("reference_chain")
    if ch:
        for w in ch["walls"]:
            w["a"], w["b"] = p2(w["a"]), p2(w["b"])
            w["length_ft"] *= f
            w["s0_ft"] *= f
    lo, hi = np.array(sc["roi"]["min"], float), np.array(sc["roi"]["max"], float)
    sc["roi"] = {"min": (m + f * (lo - m) - 0.5).tolist(), "max": (m + f * (hi - m) + 0.5).tolist()}
    return sc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--c2scene", type=Path, required=True)
    ap.add_argument("--gt", type=Path, required=True)
    ap.add_argument("--e2e", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--res", type=float, default=0.05)
    ap.add_argument("--stride", type=int, default=8)
    ap.add_argument("--max-angle", type=float, default=75.0)
    a = ap.parse_args()
    t0 = time.time()
    gt = json.loads(a.gt.read_text())
    info = json.loads((a.e2e / "e2e_info.json").read_text())
    e = info["applied_scale_error_pct"] / 100
    T = mat4_from_list(gt["frame"]["from_source"])  # scan -> meter frame
    Ti = np.linalg.inv(T)
    d = read_ply(a.e2e / "src/cloud.ply")
    X = np.c_[d["x"], d["y"], d["z"]].astype(float) @ Ti[:3, :3].T + Ti[:3, 3]
    sc = scale_c2(json.loads(a.c2scene.read_text()), e)
    lo, hi = np.array(sc["roi"]["min"], float), np.array(sc["roi"]["max"], float)
    near = np.all((X[:, :2] > lo[:2] - 15) & (X[:, :2] < hi[:2] + 15), axis=1)
    X = unique_endpoints(X[near], a.res)
    N = normals_pca(X)
    flip = np.diag([1.0, -1.0, -1.0])  # ARKit camera axes -> OpenCV
    cams = []
    for c in json.loads((a.e2e / "src/cameras.json").read_text()):
        P = Ti @ mat4_from_list(c["pose"])
        R_wc = P[:3, :3] @ flip
        R = R_wc.T
        t = -R @ P[:3, 3]
        fx, fy, cx, cy = c["intrinsics"]
        cams.append(Camera(c["image"], R, t, fx, fy, cx, cy, c["w"], c["h"]))
    rays = [depth_rays(cm, X, a.stride, a.max_angle, N) for cm in cams]
    O = opening_points(sc, a.res)
    extra = []
    if len(O):
        for cm in cams:
            Xc = O @ cm.R.T + cm.t
            ok = Xc[:, 2] > 0.3
            u = cm.fx * Xc[:, 0] / np.maximum(Xc[:, 2], 1e-6) + cm.cx
            v = cm.fy * Xc[:, 1] / np.maximum(Xc[:, 2], 1e-6) + cm.cy
            ok &= (u >= 0) & (u < cm.w) & (v >= 0) & (v < cm.h)
            if ok.any():
                extra.append((cm.center, O[ok]))
    g = carve(lo, hi, a.res, rays, extra)
    syn = []
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
        Cc = lo[:2] + (np.stack([I, J], -1) + 0.5) * a.res
        inside = poly.contains_points(Cc.reshape(-1, 2)).reshape(I.shape)
        for i, j in zip(I[inside], J[inside]):
            g.state[i, j, max(k0, 0):min(k1, g.shape[2])] = OCCUPIED
        syn.append(o["id"])
    a.out.mkdir(parents=True, exist_ok=True)
    g.save(a.out / "grid.npz")
    (a.out / "c2scene.json").write_text(json.dumps(sc, indent=1))
    rep = {"mode": "recon", "scale_error_pct": 100 * e, "res": a.res, "stride": a.stride, "cameras": len(cams),
           "points": int(len(X)), "opening_points": int(len(O)), "synthetic": syn, "shape": list(g.shape),
           "counts": {k: int((g.state == v).sum()) for k, v in (("unknown", UNKNOWN), ("free", FREE), ("occupied", OCCUPIED))},
           "runtime_s": round(time.time() - t0, 1)}
    (a.out / "grid.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep))


if __name__ == "__main__":
    main()
