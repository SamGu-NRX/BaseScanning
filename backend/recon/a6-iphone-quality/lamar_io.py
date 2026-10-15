"""LaMAR LIN helpers: ARKit trajectories (raw sessions) and GT poses (COLMAP subset, laser-registered)."""
import json, os, numpy as np
from scipy.spatial.transform import Rotation
ROOT = os.path.expanduser("~/lamar")

def load_gt():
    return json.load(open(f"{ROOT}/gt_ios.json"))

def gt_center(g):
    R = np.array(g["R"]); t = np.array(g["t"]); return -R.T @ t

def load_arkit(session):
    """timestamp -> (R_wc 3x3, c 3) sensor-to-world, as stored in Capture trajectories.txt."""
    out = {}
    for line in open(f"{ROOT}/raw/{session}/trajectories.txt"):
        if line.startswith("#"): continue
        p = [x.strip() for x in line.split(",")]
        ts = p[0]; qw, qx, qy, qz, tx, ty, tz = map(float, p[2:9])
        out[ts] = (Rotation.from_quat([qx, qy, qz, qw]).as_matrix(), np.array([tx, ty, tz]))
    return out

def load_intrinsics(session):
    out = {}
    for line in open(f"{ROOT}/raw/{session}/sensors.txt"):
        if line.startswith("#"): continue
        p = [x.strip() for x in line.split(",")]
        out[p[0].replace("cam_phone_", "")] = [float(x) for x in p[4:10]]  # w h fx fy cx cy
    return out

def umeyama(src, dst, scale=True):
    mu_s, mu_d = src.mean(0), dst.mean(0); xs, xd = src - mu_s, dst - mu_d
    U, S, Vt = np.linalg.svd(xd.T @ xs / len(src)); D = np.eye(3)
    if np.linalg.det(U @ Vt) < 0: D[2, 2] = -1
    R = U @ D @ Vt; s = (S * np.diag(D)).sum() / xs.var(0).sum() if scale else 1.0
    return s, R, mu_d - s * R @ mu_s

def matched(session, gt=None):
    gt = gt or load_gt(); ak = load_arkit(session); rows = []
    for name, g in gt.items():
        if not name.startswith(session + "/"): continue
        ts = name.split("/")[1][:-4]
        if ts in ak: rows.append((int(ts), name, gt_center(g), ak[ts][1], ak[ts][0], g))
    rows.sort(key=lambda r: r[0]); return rows
