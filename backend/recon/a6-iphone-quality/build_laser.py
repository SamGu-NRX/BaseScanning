"""Per-segment laser cloud + check of the laser lookup against the device's own LiDAR depth (06-17 session)."""
import sys, os, json, numpy as np, cv2
sys.path.insert(0, os.path.dirname(__file__))
from lamar_io import *
from laser import build_cloud, render_depth, lookup
HERE = os.path.dirname(__file__); gt = load_gt()
segs = json.load(open(f"{HERE}/results/segments.json"))
os.makedirs(f"{ROOT}/laser", exist_ok=True)
rep = {}
for seg in segs:
    C = np.array([gt_center(gt[v]) for v in seg["gt_views"]])
    P = build_cloud(C, radius=15.0, voxel=0.02, step=5, cache=f"{ROOT}/laser/{seg['id']}.npz")
    r = dict(points=len(P)); cov = []; chk = []
    for v in seg["gt_views"]:
        g = gt[v]; _, w, h, fx, fy, cx, cy = g["cam"]
        D = render_depth(P, g["R"], g["t"], [fx, fy, cx, cy], w, h)
        cov.append(float((D > 0).mean()))
        dp = f"{ROOT}/colmap/LIN/ios_depth/{v[:-4]}.png"
        if os.path.exists(dp):
            L = cv2.imread(dp, cv2.IMREAD_ANYDEPTH).astype(np.float64) / 1000
            cf = cv2.imread(dp.replace("ios_depth", "ios_depth_confidence"), cv2.IMREAD_ANYDEPTH)
            hh, ww = L.shape; vv, uu = np.mgrid[0:hh, 0:ww]
            uv = np.stack([(uu.ravel() + 0.5) * w / ww, (vv.ravel() + 0.5) * h / hh], 1)
            ld = lookup(D, uv); lm = L.ravel(); ok = np.isfinite(ld) & (lm > 0.3) & (cf.ravel() == 2) & (ld < 5)
            if ok.sum() > 50: chk.append(np.abs(lm[ok] - ld[ok]))
    r["laser_pixel_coverage_med"] = float(np.median(cov))
    if chk:
        a = np.concatenate(chk); r["lidar_vs_laser_abs_cm"] = [float(np.median(a) * 100), float(np.percentile(a, 90) * 100)]; r["lidar_px"] = int(len(a))
    rep[seg["id"]] = r; print(seg["id"], r, flush=True)
json.dump(rep, open(f"{HERE}/results/laser_check.json", "w"), indent=1)
