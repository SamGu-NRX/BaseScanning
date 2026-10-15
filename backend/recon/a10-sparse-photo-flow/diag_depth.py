"""A10 diagnostic: per-view depth scale of a feed-forward run vs the ETH3D laser (median ratio at laser pixels).
    python diag_depth.py <scene> <quality> <set> <method>"""
import sys, json
from pathlib import Path
import numpy as np
import open3d as o3d
import run as R
scene, quality, set_name, method = sys.argv[1:5]
out = R.RUNS / scene / quality / set_name / method
scan = sorted((Path.home() / f"a2/data/eth3d/{scene}/dslr_scan_eval").glob("*.ply"))
L = np.concatenate([np.asarray(o3d.io.read_point_cloud(str(p)).voxel_down_sample(0.05).points) for p in scan])
gt = R.gt_c2w(scene)
mem = R.members(scene, set_name)
src = np.array([np.load(out / "preds" / f"{m}.npz")["cam_to_world"][:3, 3] for m in mem])
dst = np.array([gt[m][:3, 3] for m in mem])
sc, _, _ = R.umeyama(src, dst)
rows = []
for m in mem:
    z = np.load(out / "preds" / f"{m}.npz")
    d = np.where(z["valid"], z["depth"], np.nan) * sc
    fx, fy, cx, cy = map(float, z["intrinsics"])
    T = gt[m]; Rcw = T[:3, :3].T; pc = (L - T[:3, 3]) @ Rcw.T
    ok = pc[:, 2] > 0.5
    pc = pc[ok]
    u = np.round(fx * pc[:, 0] / pc[:, 2] + cx).astype(int); v = np.round(fy * pc[:, 1] / pc[:, 2] + cy).astype(int)
    h, w = d.shape
    ok = (u >= 0) & (u < w) & (v >= 0) & (v < h)
    zb = np.full((h, w), np.inf)
    np.minimum.at(zb, (v[ok], u[ok]), pc[ok, 2])
    msk = np.isfinite(zb) & np.isfinite(d)
    r = d[msk] / zb[msk]
    rows.append((m, float(np.median(r)) if msk.sum() > 50 else None, int(msk.sum()), float(np.median(zb[msk])) if msk.sum() else None))
for r in rows: print(r)
rr = np.array([r[1] for r in rows if r[1]])
print("median", np.median(rr), "p10", np.percentile(rr, 10), "p90", np.percentile(rr, 90))
