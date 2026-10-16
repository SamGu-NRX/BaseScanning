"""ETH3D facade helpers: poses, laser scan, z-buffered depth maps, and 3D ground truth for PR #13's
hand-clicked windows and door.

GT method: each clicked pixel (PR #13 keyframes are the undistorted images at 1/4 of 6048 px, so
x 2048/1512 reaches our 2048 px frames) is back-projected with the laser-scan depth rendered into
that frame, and the per-view 3D points are averaged across the 2-3 views PR #13 clicked.
"""

import json
import subprocess
from pathlib import Path

import numpy as np
from plyfile import PlyData

from common import B1, STORE, WORK

HOUSE = STORE / "internal/data/houses/eth3d-facade"
SCAN_DIR = WORK / "gt/facade/dslr_scan_eval"
KEYFRAME_TO_FRAME = 2048 / 1512
ROI = np.array([[-6.0, -34.0], [26.0, -5.0]])


def qvec2rot(q):
    w, x, y, z = q
    return np.array([
        [1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * w * z, 2 * x * z + 2 * w * y],
        [2 * x * y + 2 * w * z, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * w * x],
        [2 * x * z - 2 * w * y, 2 * y * z + 2 * w * x, 1 - 2 * x * x - 2 * y * y]])


def load_cameras():
    cams = {}
    for line in open(HOUSE / "calibration/cameras.txt"):
        if line.startswith("#") or not line.strip():
            continue
        p = line.split()
        fx, fy, cx, cy = map(float, p[4:8])
        cams[int(p[0])] = {"w": int(p[2]), "h": int(p[3]), "K": np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])}
    return cams


def load_poses():
    """name -> dict(R, t, K, w, h) with x_cam = R x_world + t (COLMAP convention)."""
    cams = load_cameras()
    poses = {}
    for line in open(HOUSE / "calibration/images.txt"):
        if line.startswith("#") or not line.strip():
            continue
        p = line.split()
        if len(p) < 10:
            continue
        q = np.array(list(map(float, p[1:5])))
        t = np.array(list(map(float, p[5:8])))
        name = Path(p[9]).stem
        cam = cams[int(p[8])]
        poses[name] = {"R": qvec2rot(q), "t": t, **cam}
    return poses


def load_scan(cache=WORK / "gt/facade_roi.npy"):
    if cache.exists():
        return np.load(cache)
    txt = (SCAN_DIR / "scan_alignment.mlp").read_text()
    mats = {}
    for block in txt.split("<MLMesh")[1:]:
        name = block.split('filename="')[1].split('"')[0]
        nums = block.split("<MLMatrix44>")[1].split("</MLMatrix44>")[0].split()
        mats[name] = np.array(list(map(float, nums))).reshape(4, 4)
    pts = []
    for name, M in mats.items():
        v = PlyData.read(str(SCAN_DIR / name))["vertex"]
        xyz = np.stack([v["x"], v["y"], v["z"]], 1).astype(np.float64)
        xyz = xyz @ M[:3, :3].T + M[:3, 3]
        keep = (xyz[:, 0] > ROI[0, 0]) & (xyz[:, 0] < ROI[1, 0]) & (xyz[:, 1] > ROI[0, 1]) & (xyz[:, 1] < ROI[1, 1])
        pts.append(xyz[keep].astype(np.float32))
    pts = np.concatenate(pts)
    np.save(cache, pts)
    return pts


def depth_map(pts, pose, down=2):
    """Z-buffer of scan points in the frame at 1/down resolution; inf where empty."""
    Xc = pts @ pose["R"].T.astype(np.float32) + pose["t"].astype(np.float32)
    z = Xc[:, 2]
    ok = z > 0.3
    Xc, z = Xc[ok], z[ok]
    K = pose["K"]
    u = (K[0, 0] * Xc[:, 0] / z + K[0, 2]) / down
    v = (K[1, 1] * Xc[:, 1] / z + K[1, 2]) / down
    W, H = pose["w"] // down, pose["h"] // down
    ui, vi = u.astype(int), v.astype(int)
    ok = (ui >= 0) & (ui < W) & (vi >= 0) & (vi < H)
    D = np.full(H * W, np.inf, np.float32)
    np.minimum.at(D, vi[ok] * W + ui[ok], z[ok])
    return D.reshape(H, W)


def sample_depth(D, u, v, down=2, r=3):
    """Robust depth at full-res pixel (u, v): 25th percentile of finite z-buffer values nearby."""
    x, y = int(u / down), int(v / down)
    for rr in (r, 2 * r, 4 * r):
        patch = D[max(0, y - rr): y + rr + 1, max(0, x - rr): x + rr + 1]
        f = patch[np.isfinite(patch)]
        if f.size >= 3:
            return float(np.percentile(f, 25))
    return np.nan


def backproject(pose, u, v, z):
    K = pose["K"]
    xc = np.array([(u - K[0, 2]) / K[0, 0] * z, (v - K[1, 2]) / K[1, 1] * z, z])
    return pose["R"].T @ (xc - pose["t"])


def camera_center(pose):
    return -pose["R"].T @ pose["t"]


def project(pose, X):
    Xc = pose["R"] @ X + pose["t"]
    if Xc[2] <= 0:
        return None
    K = pose["K"]
    return np.array([K[0, 0] * Xc[0] / Xc[2] + K[0, 2], K[1, 1] * Xc[1] / Xc[2] + K[1, 2]])


def pr13_annotations(repo=WORK / "house-scanning"):
    txt = subprocess.run(["git", "-C", str(repo), "show", "pr13:verification/scenes/eth3d-facade/annotations.json"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(txt)


def build_gt():
    poses = load_poses()
    pts = load_scan()
    ann = pr13_annotations()
    depth_cache = {}
    gt = []
    for f in ann["features"]:
        per_view = {}
        for view, clicks in f["points_px"].items():
            if view not in depth_cache:
                depth_cache[view] = depth_map(pts, poses[view])
            D = depth_cache[view]
            P = {}
            for k, (u, v) in clicks.items():
                u, v = u * KEYFRAME_TO_FRAME, v * KEYFRAME_TO_FRAME
                P[k] = backproject(poses[view], u, v, sample_depth(D, u, v)).tolist()
            per_view[view] = P
        arr = {k: np.nanmean([per_view[vw][k] for vw in per_view], axis=0) for k in ["left", "right", "bottom", "top"]}
        mid_h = (arr["left"] + arr["right"]) / 2
        up = arr["top"] - arr["bottom"]
        # left/right clicks sit at sill level for windows, mid-height for the door
        center = mid_h + (up / 2 if f["type"] == "window" else 0 * up)
        center[2] = (arr["bottom"][2] + arr["top"][2]) / 2
        spread = max(float(np.linalg.norm(np.array(per_view[a]["left"]) - np.array(per_view[b]["left"])))
                     for a in per_view for b in per_view)
        gt.append({"label": f["label"], "type": f["type"], "center": center.tolist(),
                   "width_m": float(np.linalg.norm(arr["right"] - arr["left"])),
                   "height_m": float(arr["top"][2] - arr["bottom"][2]),
                   "view_disagreement_m": spread, "views": per_view})
    out = B1 / "results/eth3d_gt.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(gt, indent=1))
    return gt


if __name__ == "__main__":
    for g in build_gt():
        print(g["label"], np.round(g["center"], 2), f"w={g['width_m']:.2f} h={g['height_m']:.2f} views_disagree={g['view_disagreement_m']:.2f}")
