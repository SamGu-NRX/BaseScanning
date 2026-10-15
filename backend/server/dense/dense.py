"""Metric alignment helpers and π³ / DA3 window fusion.

Model calls stay behind callables so CPU tests never load weights. π³ and DA3
on COLMAP poses are coverage only (A10, p90 7–21 in); the caller decides that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from server.common.geometry import umeyama as common_umeyama
from server.dense.params import DenseParams
from server.dense.windows import flag_window


@dataclass
class WindowFuse:
    index: int
    frame_ids: list[int]
    scale: float
    rms_m: float
    flagged: bool
    points: np.ndarray = field(repr=False)
    reason: str = ""


def umeyama(src: np.ndarray, dst: np.ndarray, with_scale: bool = True) -> np.ndarray:
    """4×4 Sim(3). ``server.common.geometry.umeyama`` returns ``(s, R, t)``."""
    scale, rotation, translation = common_umeyama(src, dst, with_scale=with_scale)
    transform = np.eye(4)
    transform[:3, :3] = float(scale) * np.asarray(rotation, float)
    transform[:3, 3] = np.asarray(translation, float)
    return transform


def align_centers(pred: np.ndarray, sfm: np.ndarray) -> tuple[np.ndarray, float, float]:
    pred = np.asarray(pred, float)
    sfm = np.asarray(sfm, float)
    transform = umeyama(pred, sfm, with_scale=True)
    aligned = pred @ transform[:3, :3].T + transform[:3, 3]
    rms = float(np.sqrt(np.mean(np.sum((aligned - sfm) ** 2, axis=1))))
    scale = float(np.cbrt(abs(np.linalg.det(transform[:3, :3]))))
    return transform, rms, scale


def fuse_windows(
    windows: list[list[int]],
    pred_centers: list[np.ndarray],
    sfm_centers: list[np.ndarray],
    pred_points: list[np.ndarray],
    params: DenseParams,
) -> tuple[list[WindowFuse], float]:
    """Align each window and flag ``window_pose_inconsistent``.

    ``pred_points[k]`` are that window's points in the predicted camera frame,
    already masked. Flagged windows are returned but must not enter the cloud.
    """
    scales = []
    aligned: list[tuple[float, float, np.ndarray]] = []
    for pred, sfm, pts in zip(pred_centers, sfm_centers, pred_points, strict=True):
        transform, rms, scale = align_centers(pred, sfm)
        scales.append(scale)
        world = np.asarray(pts, float) @ transform[:3, :3].T + transform[:3, 3]
        aligned.append((rms, scale, world))
    median = float(np.median(scales)) if scales else 1.0
    fused: list[WindowFuse] = []
    for k, (members, (rms, scale, world)) in enumerate(zip(windows, aligned, strict=True)):
        bad = flag_window(rms, scale, median, params)
        fused.append(
            WindowFuse(
                index=k,
                frame_ids=list(members),
                scale=scale,
                rms_m=rms,
                flagged=bad,
                points=world,
                reason="window_pose_inconsistent" if bad else "",
            )
        )
    return fused, median


def subsample_indices(
    points: np.ndarray, stride: int = 4, voxel_m: float = 0.02, cap: int = 500_000, seed: int = 0
) -> np.ndarray:
    """Indices ``subsample`` keeps, so a parallel track list stays aligned."""
    pts = np.asarray(points, float)
    n = len(pts)
    if n == 0:
        return np.zeros(0, np.int64)
    idx = np.arange(n)[:: max(1, stride)]
    kept = pts[idx]
    if voxel_m > 0 and len(kept):
        keys = np.floor(kept / voxel_m).astype(np.int64)
        _, uniq = np.unique(keys, axis=0, return_index=True)
        uniq = np.sort(uniq)
        idx = idx[uniq]
        kept = kept[uniq]
    if len(idx) > cap:
        rng = np.random.default_rng(seed)
        idx = idx[rng.choice(len(idx), cap, replace=False)]
    return np.asarray(idx, np.int64)


def subsample(
    points: np.ndarray, stride: int = 4, voxel_m: float = 0.02, cap: int = 500_000, seed: int = 0
) -> np.ndarray:
    pts = np.asarray(points, float)
    if len(pts) == 0:
        return pts.reshape(0, 3)
    return pts[subsample_indices(pts, stride=stride, voxel_m=voxel_m, cap=cap, seed=seed)]


def resolve_second_opinion(mode: str, flow: str) -> str:
    """``auto`` is DA3 in the photo flow only. Guided flow stays ``none``."""
    if mode == "auto":
        return "da3" if flow == "photo" else "none"
    return mode
