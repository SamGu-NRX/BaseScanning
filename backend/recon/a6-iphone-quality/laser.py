"""Laser ground truth for LaMAR LIN: NavVis depth maps (rendered by LaMAR from the laser mesh) back-projected
with their GT poses into one cloud, then z-buffered into any GT camera to give per-pixel laser depth."""
import json, os, subprocess, numpy as np, cv2
from concurrent.futures import ThreadPoolExecutor
ROOT = os.path.expanduser("~/lamar")
NAV = json.load(open(f"{ROOT}/gt_navvis.json"))

def _c(g): R = np.array(g["R"]); return -R.T @ np.array(g["t"])

def fetch_navvis_depth(names):
    need = [f"LIN/navvis_depth/{n[:-4]}.png" for n in names if not os.path.exists(f"{ROOT}/colmap/LIN/navvis_depth/{n[:-4]}.png")]
    if need:
        open(f"{ROOT}/_need.txt", "w").write("\n".join(need))
        subprocess.run(["/usr/bin/python3", f"{ROOT}/fetch_par.py", f"{ROOT}/_need.txt"], cwd=ROOT, check=True)

def build_cloud(query_centers, radius=20.0, voxel=0.02, step=3, max_depth=25.0, cache=None):
    if cache and os.path.exists(cache): return np.load(cache)["pts"]
    Q = np.asarray(query_centers)
    names = [n for n, g in NAV.items() if np.linalg.norm(Q - _c(g), axis=1).min() < radius]
    fetch_navvis_depth(names)
    def one(n):
        g = NAV[n]; _, w, h, fx, fy, cx, cy = g["cam"]
        d = cv2.imread(f"{ROOT}/colmap/LIN/navvis_depth/{n[:-4]}.png", cv2.IMREAD_ANYDEPTH)
        if d is None: return np.zeros((0, 3))
        d = d[::step, ::step].astype(np.float64) / 1000.0
        v, u = np.mgrid[0:h:step, 0:w:step]
        m = (d > 0.3) & (d < max_depth)
        x = np.stack([(u[m] + 0.5 - cx) / fx * d[m], (v[m] + 0.5 - cy) / fy * d[m], d[m]], 1)
        R = np.array(g["R"]); t = np.array(g["t"])
        return (x - t) @ R
    with ThreadPoolExecutor(4) as ex: parts = list(ex.map(one, names))
    P = np.concatenate(parts)
    k = np.floor(P / voxel).astype(np.int64)
    _, idx = np.unique(k, axis=0, return_index=True)
    P = P[idx].astype(np.float32)
    if cache: np.savez(cache, pts=P, navvis=np.array(names))
    return P

def render_depth(P, R_cw, t_cw, K, w, h, s=0.25, splat=1):
    """Laser z-depth at resolution s*(w,h); 0 = no laser. K = fx, fy, cx, cy at full resolution."""
    fx, fy, cx, cy = [k * s for k in K]; W, H = int(round(w * s)), int(round(h * s))
    X = P @ np.asarray(R_cw).T + np.asarray(t_cw)
    z = X[:, 2]; m = z > 0.2; X, z = X[m], z[m]
    u = np.floor(X[:, 0] / z * fx + cx).astype(np.int64); v = np.floor(X[:, 1] / z * fy + cy).astype(np.int64)
    D = np.full((H, W), np.inf, np.float32)
    for du in range(-splat, splat + 1):
        for dv in range(-splat, splat + 1):
            uu, vv = u + du, v + dv; k = (uu >= 0) & (uu < W) & (vv >= 0) & (vv < H)
            np.minimum.at(D, (vv[k], uu[k]), z[k])
    D[~np.isfinite(D)] = 0
    return D

def lookup(D, uv_full, s=0.25, edge_rel=0.05):
    """Laser depth at full-res pixels (continuous coords); NaN where missing or on a depth edge."""
    H, W = D.shape
    u = np.floor(uv_full[:, 0] * s).astype(int); v = np.floor(uv_full[:, 1] * s).astype(int)
    out = np.full(len(u), np.nan)
    k = (u >= 1) & (u < W - 1) & (v >= 1) & (v < H - 1)
    uu, vv = u[k], v[k]
    nb = np.stack([D[vv + a, uu + b] for a in (-1, 0, 1) for b in (-1, 0, 1)], 1)
    c = D[vv, uu]
    ok = (c > 0) & (nb.min(1) > 0) & ((nb.max(1) - nb.min(1)) < edge_rel * c)
    r = np.where(ok, c, np.nan); out[np.where(k)[0]] = r
    return out

def backproject(uv, depth, R_cw, t_cw, K):
    fx, fy, cx, cy = K
    x = np.stack([(uv[:, 0] - cx) / fx * depth, (uv[:, 1] - cy) / fy * depth, depth], 1)
    return (x - np.asarray(t_cw)) @ np.asarray(R_cw)
