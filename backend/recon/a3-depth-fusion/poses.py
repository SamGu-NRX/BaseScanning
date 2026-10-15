"""Pose modes and per-image depth scaling for fusion.

Pose modes (all expressed in ETH3D's world, OpenCV camera-to-world):
- `gt`: ETH3D's laser-registered poses (upper bound).
- `<profile>@<seed>`: the packet generator's ARKit drift model (`packetgen.noise.apply_drift`,
  profiles fitted to PR #12's ADVIO / MARViN measurements) applied in a gravity-levelled world, in
  capture order (DSC number), then mapped back. Frame 0 sits on its true pose; later frames carry
  the session scale error, yaw and position random walks and jitter. Tracking resets are disabled
  (a reset starts a new world, which fusion would have to treat as a separate map).

Depth scaling modes (one multiplicative factor per image unless noted):
- `raw`: the model's metric depth as is.
- `feat`: median ratio to simulated ARKit raw feature points (ETH3D's SfM points the photo
  observed, with the session scale and 2% depth noise, as `packetgen.sensors.feature_points`).
- `tri`: median ratio to SIFT matches triangulated with the given poses (PR #12 triangulate.py),
  image-only and consistent with whatever poses the fusion uses.
- `lidar`: median ratio to simulated LiDAR depth (laser scan rendered as depth, 0.3-5 m, block
  averaged to 256 px wide, noise 1 cm + 0.5%); LiDAR depth then replaces mono depth where present.
- `oracle`: median ratio to the laser-scan depth (diagnostic: removes scale, keeps shape error).
"""

from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np

from common import ETH3D_DIR, WIDTH, View, eval_image, scene_views
from evals.coverage import level_world
from evals.eth3d import scan_points
from evals.triangulate import match, sample_depth, triangulate_pair
from packetgen.noise import apply_drift, load_profiles

LIDAR_RANGE_M = (0.3, 5.0)
LIDAR_WIDTH = 256
FEATURE_MAX = 400
FEATURE_DEPTH_NOISE = 0.02


def cam_to_world(v: View) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = v.R_wc.T
    T[:3, 3] = v.center
    return T


@lru_cache(maxsize=4)
def level_rotation(scene: str) -> np.ndarray:
    views = scene_views(scene)
    R, _, _ = level_world(views, scan_points(scene)[::3].astype(np.float64))
    return R


def poses(scene: str, mode: str) -> tuple[dict[str, np.ndarray], dict]:
    views = scene_views(scene)
    gt = {v.name: cam_to_world(v) for v in views}
    if mode == "gt":
        return gt, {"mode": "gt"}
    profile_name, seed = mode.split("@")
    profile = load_profiles()[profile_name]
    R = level_rotation(scene)
    names = sorted(gt)
    Rc = np.stack([R @ gt[n][:3, :3] for n in names])
    c = np.stack([R @ gt[n][:3, 3] for n in names])
    rng = np.random.default_rng(int(seed))
    R_obs, c_obs, _epoch, draw = apply_drift(Rc, c, profile, rng, reset_index=None)
    out = {}
    for i, n in enumerate(names):
        T = np.eye(4)
        T[:3, :3] = R.T @ R_obs[i]
        T[:3, 3] = R.T @ c_obs[i]
        out[n] = T
    err = np.linalg.norm(c_obs - c, axis=1)
    path = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(c[:, [0, 2]], axis=0), axis=1))])
    info = {
        "mode": mode,
        "session_scale": draw.scales[0],
        "session_multiplier": draw.session_multiplier,
        "path_m": float(path[-1]),
        "centre_error_m": {"median": float(np.median(err)), "p90": float(np.percentile(err, 90)), "max": float(err.max())},
        "yaw_error_deg_final": float(draw.yaw_error_deg[-1]),
    }
    return out, info


@lru_cache(maxsize=4)
def _sparse_model(scene: str) -> tuple[dict[str, tuple[np.ndarray, np.ndarray]], dict[int, np.ndarray]]:
    """Per image: (uv at full resolution, point ids); and point id -> xyz (ETH3D world)."""
    cal = ETH3D_DIR / scene / "dslr_calibration_undistorted"
    xyz = {}
    for line in (cal / "points3D.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            f = line.split()
            xyz[int(f[0])] = np.array(list(map(float, f[1:4])))
    lines = [x for x in (cal / "images.txt").read_text().splitlines() if not x.startswith("#")]
    obs = {}
    for pose_line, pts_line in zip(lines[0::2], lines[1::2]):
        if not pose_line.strip():
            continue
        name = pose_line.split()[9].split("/")[-1].rsplit(".", 1)[0]
        a = np.array(pts_line.split(), float).reshape(-1, 3) if pts_line.strip() else np.zeros((0, 3))
        a = a[a[:, 2] >= 0]
        obs[name] = (a[:, :2], a[:, 2].astype(np.int64))
    return obs, xyz


def feature_scale(scene: str, v: View, depth: np.ndarray, session_scale: float, rng) -> tuple[float | None, int]:
    obs, xyz = _sparse_model(scene)
    uv_full, ids = obs.get(v.name, (np.zeros((0, 2)), np.zeros(0, np.int64)))
    if len(ids) == 0:
        return None, 0
    X = np.stack([xyz[i] for i in ids])
    z_true = (X @ v.R_wc.T + v.t_wc)[:, 2]
    keep = (z_true > 0.3) & (z_true < 20.0)
    idx = np.flatnonzero(keep)
    if len(idx) > FEATURE_MAX:
        idx = rng.choice(idx, FEATURE_MAX, replace=False)
    h, w = depth.shape
    s = w / v.width
    uv = uv_full[idx] * s - 0.5  # COLMAP continuous pixels -> OpenCV integer centres, resized
    z_obs = session_scale * z_true[idx] * (1 + rng.normal(0, FEATURE_DEPTH_NOISE, len(idx)))
    z_pred = sample_depth(depth, uv)
    ok = np.isfinite(z_pred)
    if ok.sum() < 20:
        return None, int(ok.sum())
    return float(np.median(z_obs[ok] / z_pred[ok])), int(ok.sum())


def neighbours(views: list[View], T: dict[str, np.ndarray], k: int = 6) -> list[tuple[str, str]]:
    pairs = set()
    for v in views:
        fv = T[v.name][:3, 2]
        cands = [u for u in views if u.name != v.name and T[u.name][:3, 2] @ fv > 0.5]
        cands.sort(key=lambda u: np.linalg.norm(T[u.name][:3, 3] - T[v.name][:3, 3]))
        for u in cands[:k]:
            pairs.add(tuple(sorted((v.name, u.name))))
    return sorted(pairs)


def triangulated_scales(scene: str, T: dict[str, np.ndarray], depth: dict[str, np.ndarray],
                        feats_cache: dict | None = None) -> dict[str, tuple[float | None, int]]:
    """Per image: median(z_tri / z_pred) over SIFT matches with its 6 nearest same-facing views,
    triangulated with the poses T (PR #12's filters: 2 px reprojection, 2 degree ray angle)."""
    views = [v for v in scene_views(scene) if v.name in depth]
    feats = feats_cache if feats_cache is not None else {}
    sift = cv2.SIFT_create(nfeatures=4000)
    K = {}
    for v in views:
        rgb, K[v.name] = eval_image(v)
        if v.name not in feats:
            kps, desc = sift.detectAndCompute(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), None)
            feats[v.name] = (np.array([kp.pt for kp in kps], np.float64).reshape(-1, 2),
                             np.zeros((0, 128), np.float32) if desc is None else desc)
    ratios: dict[str, list[np.ndarray]] = {v.name: [] for v in views}
    for a, b in neighbours(views, T):
        idx = match(feats[a][1], feats[b][1])
        if len(idx) < 8:
            continue
        xa, xb = feats[a][0][idx[:, 0]], feats[b][0][idx[:, 1]]
        pts = triangulate_pair(K[a], T[a], K[b], T[b], xa, xb, 2.0, 2.0)
        X = pts.X[pts.keep]
        for n, x in ((a, xa[pts.keep]), (b, xb[pts.keep])):
            z_tri = (X - T[n][:3, 3]) @ T[n][:3, 2]
            z_pred = sample_depth(depth[n], x)
            ok = np.isfinite(z_pred)
            ratios[n].append(z_tri[ok] / z_pred[ok])
    out = {}
    for n, rs in ratios.items():
        r = np.concatenate(rs) if rs else np.empty(0)
        out[n] = (float(np.median(r)), len(r)) if len(r) >= 20 else (None, len(r))
    return out


def simulated_lidar(oracle: np.ndarray, rng) -> np.ndarray:
    """LiDAR-like depth on the eval grid from the laser-scan depth: range-limited, block-averaged
    to LIDAR_WIDTH px wide (then nearest-upsampled), with depth noise."""
    h, w = oracle.shape
    f = max(1, w // LIDAR_WIDTH)
    hh, ww = h // f, w // f
    d = oracle[: hh * f, : ww * f].reshape(hh, f, ww, f)
    with np.errstate(invalid="ignore"):
        low = np.nanmedian(d.transpose(0, 2, 1, 3).reshape(hh, ww, f * f), axis=2)
    low = np.where((low >= LIDAR_RANGE_M[0]) & (low <= LIDAR_RANGE_M[1]), low, np.nan)
    low = low + rng.normal(0, 1, low.shape) * (0.01 + 0.005 * np.nan_to_num(low))
    up = np.repeat(np.repeat(low, f, axis=0), f, axis=1)
    out = np.full((h, w), np.nan, np.float32)
    out[: up.shape[0], : up.shape[1]] = up
    return out


def median_ratio(ref: np.ndarray, depth: np.ndarray, min_px: int = 200) -> float | None:
    ok = np.isfinite(ref) & np.isfinite(depth) & (depth > 0)
    if ok.sum() < min_px:
        return None
    return float(np.median(ref[ok] / depth[ok]))


__all__ = ["poses", "feature_scale", "triangulated_scales", "simulated_lidar", "median_ratio", "cam_to_world", "WIDTH"]
