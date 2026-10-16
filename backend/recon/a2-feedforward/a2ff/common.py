"""Shared paths, scene inputs and output conventions for the A2 feed-forward runs.

Scoring reuses PR #12's `evals` package (vendored under ../evals at house-scanning commit
24793c3, branch t3/evals): ETH3D laser scans, fixed per-seed evaluation sets, point-pair length
errors. Every runner writes PR #12's prediction format, one npz per view at the 1024 px evaluation
width: `depth` (float32, NaN where invalid), `valid`, `intrinsics` [fx, fy, cx, cy] (OpenCV pixels
of the 1024 px image) and `cam_to_world` (OpenCV camera, in the frame and scale the run chose).
Extra arrays: `conf` (the model's confidence, resampled) and `model_mask` (the model's own validity
mask when it has one), so masked and unmasked scoring can both be run.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path.home() / "a2"
CODE = ROOT / "code"
os.environ.setdefault("HOUSE_SCANNING_DATA", str(ROOT / "hsdata"))
os.environ.setdefault("HF_HOME", str(ROOT / "hf"))
os.environ.setdefault("TORCH_HOME", str(ROOT / "torch-cache"))
PACKETGEN = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/packet-generator")
for p in (CODE, PACKETGEN):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import numpy as np  # noqa: E402

from evals.paths import ETH3D_DIR  # noqa: E402
from models.common import (  # noqa: E402
    Resample,
    cover_crop_resample,
    depth_to_input,
    intrinsics_to_input,
    intrinsics_to_network,
    load_rgb,
    matrix,
    to_network_image,
    vector,
)

SCENES = ("facade", "electro", "delivery_area")
WIDTH = 1024
PRED_ROOT = ROOT / "preds"
GROUP_SIZES = (1, 2, 4, 8, 16)


def scene_dir(scene: str) -> Path:
    return ETH3D_DIR / scene


def image_path(scene: str, view: str) -> Path:
    return scene_dir(scene) / f"images_{WIDTH}" / f"{view}.jpg"


def groups(scene: str) -> dict[str, list[list[str]]]:
    return json.loads((scene_dir(scene) / "subsets.json").read_text())


def group_id(n: int, members: list[str]) -> str:
    return f"n{n}-{members[0]}"


def umeyama_sim3(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """s, R, t minimising |s R src + t - dst|^2."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    U, S, Vt = np.linalg.svd(b.T @ a / len(src))
    D = np.eye(3)
    if np.linalg.det(U @ Vt) < 0:
        D[2, 2] = -1
    R = U @ D @ Vt
    s = float(np.trace(np.diag(S) @ D) / a.var(0).sum())
    return s, R, mu_d - s * R @ mu_s


def fit_intrinsics_from_local_points(local: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    """[fx, fy, cx, cy] (network pixels, OpenCV centres) of a camera-frame point map (H, W, 3),
    principal point fixed at the image centre, focal lengths by least squares."""
    h, w = local.shape[:2]
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    u, v = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
    X, Y, Z = local[..., 0], local[..., 1], local[..., 2]
    ok = np.isfinite(local).all(-1) & (Z > 1e-6)
    if mask is not None:
        ok &= mask
    x, y = X[ok] / Z[ok], Y[ok] / Z[ok]
    du, dv = u[ok] - cx, v[ok] - cy
    fx = float((x * du).sum() / (x * x).sum())
    fy = float((y * dv).sum() / (y * y).sum())
    return np.array([fx, fy, cx, cy])


def network_inputs(scene: str, members: list[str], net_w: int, net_h: int):
    """RGB network images (uint8) cover-cropped from the 1024 px images, their Resamples, and the
    dataset intrinsics of the 1024 px images."""
    rgbs, resamples = [], []
    for m in members:
        rgb = load_rgb(image_path(scene, m))
        r = cover_crop_resample(rgb.shape[1], rgb.shape[0], net_w, net_h)
        rgbs.append(to_network_image(rgb, r))
        resamples.append(r)
    return rgbs, resamples


def resample_map(a_net: np.ndarray, r: Resample) -> np.ndarray:
    """Any per-pixel float map (any sign) from the network grid to the 1024 px grid, bilinear,
    with `models.common.depth_to_input`'s pixel mapping."""
    import cv2

    u = r.sx * (np.arange(r.in_w, dtype=np.float64) + 0.5) - r.x0 - 0.5
    v = r.sy * (np.arange(r.in_h, dtype=np.float64) + 0.5) - r.y0 - 0.5
    map_x = np.broadcast_to(u[None, :], (r.in_h, r.in_w)).astype(np.float32)
    map_y = np.broadcast_to(v[:, None], (r.in_h, r.in_w)).astype(np.float32)
    a = np.nan_to_num(a_net.astype(np.float32), nan=0.0)
    return cv2.remap(a, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def write_view(out_dir: Path, view: str, depth_net: np.ndarray, K_net: np.ndarray,
               cam_to_world: np.ndarray, r: Resample, conf_net: np.ndarray | None = None,
               mask_net: np.ndarray | None = None) -> dict:
    """Write one view's prediction at the 1024 px evaluation grid; every finite positive depth is
    kept (`valid`), the model's own mask goes to `model_mask`."""
    valid_net = np.isfinite(depth_net) & (depth_net > 0)
    depth, valid = depth_to_input(depth_net.astype(np.float32), valid_net, r)
    arrays = {
        "depth": depth.astype(np.float32),
        "valid": valid,
        "intrinsics": intrinsics_to_input(np.asarray(K_net, np.float64).reshape(4), r),
        "cam_to_world": np.asarray(cam_to_world, np.float64),
    }
    if conf_net is not None:
        arrays["conf"] = resample_map(conf_net.astype(np.float32), r).astype(np.float32)
    if mask_net is not None:
        arrays["model_mask"] = resample_map(mask_net.astype(np.float32), r) > 0.999
    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_dir / f"{view}.npz", **arrays)
    d = depth[valid]
    return {
        "valid_fraction": round(float(valid.mean()), 4),
        "median_depth_m": round(float(np.median(d)), 3) if d.size else None,
    }


def pose_file(scene: str, setting: str) -> Path:
    return PRED_ROOT / scene / "arkit_poses" / f"{setting}.json"


def load_poses(scene: str, setting: str) -> dict:
    """{view: {"T": 4x4 OpenCV camera-to-world, "K": [fx, fy, cx, cy] at 1024 px}}."""
    data = json.loads(pose_file(scene, setting).read_text())
    return {v: {"T": np.array(e["T"]), "K": np.array(e["K"])} for v, e in data["views"].items()}


__all__ = [
    "ROOT", "CODE", "SCENES", "WIDTH", "PRED_ROOT", "GROUP_SIZES", "scene_dir", "image_path",
    "groups", "group_id", "umeyama_sim3", "fit_intrinsics_from_local_points", "network_inputs",
    "write_view", "pose_file", "load_poses", "matrix", "vector", "intrinsics_to_network",
]
