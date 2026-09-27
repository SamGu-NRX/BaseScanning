# vendored from internal/modal-pipeline/housepipe/dense.py (store, 2026-09-26)
"""π³ and DA3-Large dense geometry on the refined SfM poses.

π³ does not take poses. Each overlapping window is aligned to the refined
camera centres with a similarity (the same Umeyama fit A2 uses at score time),
so the dense cloud inherits the SfM metric frame. DA3-Large is fed the refined
world-to-camera poses directly, then the same similarity removes residual scale.
"""

from __future__ import annotations

import time

import numpy as np

from server.common.geometry import apply_sim3, umeyama

PI3_REPO = "yyfz233/Pi3"
DA3_REPO = "depth-anything/DA3-LARGE-1.1"


def net_wh(width: int, height: int, max_side: int = 518) -> tuple[int, int]:
    scale = max_side / max(width, height)
    nw = max(14, int(round(width * scale / 14.0)) * 14)
    nh = max(14, int(round(height * scale / 14.0)) * 14)
    return nw, nh


def windows(n: int, size: int, stride: int) -> list[list[int]]:
    if n == 0:
        return []
    if n <= size:
        return [list(range(n))]
    out = []
    start = 0
    while start < n:
        idx = list(range(start, min(n, start + size)))
        if len(idx) >= 3:
            out.append(idx)
        if start + size >= n:
            break
        start += stride
    return out


def _resize(rgb: np.ndarray, wh: tuple[int, int]) -> np.ndarray:
    import cv2

    return cv2.resize(rgb, wh, interpolation=cv2.INTER_AREA)


def load_pi3(device: str = "cuda"):
    import torch
    from pi3.models.pi3 import Pi3

    model = Pi3.from_pretrained(PI3_REPO).to(device).eval()
    return model


def load_da3(device: str = "cuda"):
    import torch
    from depth_anything_3.api import DepthAnything3

    model = DepthAnything3.from_pretrained(DA3_REPO).to(device).eval()
    return model


def _as_numpy(value):
    import torch

    if isinstance(value, torch.Tensor):
        return value.detach().float().cpu().numpy()
    return np.asarray(value)


def _conf_mask(conf: np.ndarray, threshold: float = 0.15) -> np.ndarray:
    c = conf.reshape(conf.shape[0], conf.shape[1], -1)[..., 0]
    if c.min() < 0 or c.max() > 1.5:
        c = 1.0 / (1.0 + np.exp(-c))
    return c > threshold, c


def pi3_window(model, rgbs: list[np.ndarray]) -> dict:
    import torch

    h, w = rgbs[0].shape[:2]
    batch = torch.from_numpy(np.stack(rgbs)).permute(0, 3, 1, 2).float().cuda() / 255.0
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        out = model(batch[None])
    if not isinstance(out, dict):
        out = {k: getattr(out, k) for k in ("points", "local_points", "conf", "camera_poses") if hasattr(out, k)}
    points = _as_numpy(out["points"])[0]
    conf = _as_numpy(out["conf"])[0]
    poses = _as_numpy(out["camera_poses"])[0]
    if points.shape[-1] > 3:
        points = points[..., :3]
    return {"points": points, "conf": conf, "c2w": poses, "hw": (h, w)}


def da3_window(model, rgbs: list[np.ndarray], c2w: np.ndarray, K: np.ndarray) -> dict:
    """Feed refined poses. c2w/K are at the original image size; DA3 rescales."""
    w2c = np.stack([np.linalg.inv(T) for T in c2w])
    pred = model.inference(
        rgbs,
        extrinsics=w2c[:, :3, :],
        intrinsics=K,
        process_res=504,
        process_res_method="upper_bound_resize",
    )
    depth = np.asarray(pred.depth, np.float32)
    conf = np.asarray(pred.conf, np.float32) if pred.conf is not None else np.ones(depth.shape, np.float32)
    extr = np.asarray(pred.extrinsics, float)
    intr = np.asarray(pred.intrinsics, float)
    poses = []
    points = []
    for i in range(len(rgbs)):
        w2c_i = np.eye(4)
        w2c_i[:3] = extr[i][:3]
        c2w_i = np.linalg.inv(w2c_i)
        poses.append(c2w_i)
        h, w = depth[i].shape
        fx, fy, cx, cy = intr[i][0, 0], intr[i][1, 1], intr[i][0, 2], intr[i][1, 2]
        vs, us = np.mgrid[0:h, 0:w]
        z = depth[i]
        x = (us - cx) / fx * z
        y = (vs - cy) / fy * z
        local = np.stack([x, y, z], axis=-1)
        world = local.reshape(-1, 3) @ c2w_i[:3, :3].T + c2w_i[:3, 3]
        points.append(world.reshape(h, w, 3))
    return {
        "points": np.stack(points),
        "conf": conf,
        "c2w": np.stack(poses),
        "hw": depth.shape[1:],
    }


def _view_masks(conf: np.ndarray, n: int, hw: tuple[int, int]) -> np.ndarray:
    if conf is None:
        return np.ones((n, hw[0], hw[1]), bool)
    arr = np.asarray(conf)
    if arr.ndim == 4 and arr.shape[-1] == 1:
        arr = arr[..., 0]
    if arr.ndim == 2:
        arr = arr[None]
    masks = []
    for i in range(n):
        mask, _ = _conf_mask(arr[i])
        masks.append(mask)
    return np.stack(masks)


def _fuse_window(pred: dict, sfm_centers: np.ndarray, stride: int = 4) -> tuple[np.ndarray, dict]:
    poses = np.asarray(pred["c2w"], float)
    pred_centers = poses[:, :3, 3]
    info = {"aligned": False, "scale": None}
    points = np.asarray(pred["points"], float)
    if points.ndim == 5 and points.shape[-1] >= 3:
        points = points[..., :3]
    if len(sfm_centers) == len(pred_centers) and len(sfm_centers) >= 3 and np.isfinite(pred_centers).all():
        s, R, t = umeyama(pred_centers, sfm_centers, with_scale=True)
        if np.isfinite(s) and 1e-4 < abs(s) < 1e4:
            points = apply_sim3(points.reshape(-1, 3), s, R, t).reshape(points.shape)
            info = {"aligned": True, "scale": float(s)}
    if points.ndim != 4:
        raise RuntimeError(f"unexpected point shape {points.shape}")
    mask = _view_masks(pred.get("conf"), points.shape[0], points.shape[1:3])
    pts = points[:, ::stride, ::stride]
    keep = mask[:, ::stride, ::stride] & np.isfinite(pts).all(-1)
    return pts[keep].astype(np.float32), info


def run_dense(
    model_name: str,
    model,
    images: list[np.ndarray],
    cameras_by_name: dict[str, dict],
    order: list[str],
    *,
    window: int = 8,
) -> dict:
    t0 = time.perf_counter()
    stride = max(1, window // 2)
    groups = windows(len(order), window, stride)
    clouds = []
    scales = []
    per_window = []
    for group in groups:
        names = [order[i] for i in group]
        frames = []
        centers = []
        Ks = []
        c2ws = []
        for name in names:
            cam = cameras_by_name.get(name)
            if cam is None:
                continue
            frames.append(images[order.index(name)])
            centers.append(cam["center"])
            Ks.append(cam["K_full"])
            c2ws.append(cam["c2w"])
        if len(frames) < 3:
            continue
        wh = net_wh(frames[0].shape[1], frames[0].shape[0])
        resized = [_resize(im, wh) for im in frames]
        tw = time.perf_counter()
        if model_name == "pi3":
            pred = pi3_window(model, resized)
        elif model_name == "da3":
            # Scale intrinsics into the resized image. DA3 also resizes internally;
            # passing the resized image and its K keeps the pose-conditioned path metric.
            sx = wh[0] / frames[0].shape[1]
            sy = wh[1] / frames[0].shape[0]
            K = np.stack([
                np.array([[k[0] * sx, 0, k[2] * sx], [0, k[1] * sy, k[3] * sy], [0, 0, 1]], float)
                for k in Ks
            ])
            pred = da3_window(model, resized, np.stack(c2ws), K)
        else:
            raise RuntimeError(model_name)
        xyz, info = _fuse_window(pred, np.stack(centers))
        per_window.append({
            "views": len(frames),
            "points": int(len(xyz)),
            "seconds": round(time.perf_counter() - tw, 3),
            **info,
        })
        if len(xyz):
            clouds.append(xyz)
        if info.get("scale") is not None:
            scales.append(info["scale"])
    if clouds:
        xyz = np.concatenate(clouds, axis=0)
        if len(xyz) > 250_000:
            rng = np.random.default_rng(0)
            xyz = xyz[rng.choice(len(xyz), 250_000, replace=False)]
        q = np.round(xyz / 0.02).astype(np.int32)
        _, idx = np.unique(q, axis=0, return_index=True)
        xyz = xyz[idx]
    else:
        xyz = np.zeros((0, 3), np.float32)
    return {
        "model": model_name,
        "checkpoint": PI3_REPO if model_name == "pi3" else DA3_REPO,
        "windows": len(per_window),
        "window": window,
        "points": int(len(xyz)),
        "seconds": round(time.perf_counter() - t0, 3),
        "per_window": per_window,
        "sim3_scale_median": float(np.median(scales)) if scales else None,
        "points_xyz": xyz,
    }
