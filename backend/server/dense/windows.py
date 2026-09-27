"""Window grouping and aspect-matched network sizes."""

from __future__ import annotations

import numpy as np

from server.common.scaleio import as_pose
from server.dense.params import DenseParams


def net_wh(w: int, h: int, side: int = 518, patch: int = 14) -> tuple[int, int]:
    """Aspect-matched multiples of 14. 1920×1440 → 518×392. Never cover-crop."""
    if w <= 0 or h <= 0:
        raise ValueError("image size must be positive")
    if w >= h:
        nw = side
        nh = max(patch, int(round(side * h / w / patch) * patch))
    else:
        nh = side
        nw = max(patch, int(round(side * w / h / patch) * patch))
    return int(nw), int(nh)


def camera_forward(pose: np.ndarray) -> np.ndarray:
    """ARKit camera looks along -Z of the camera-to-world rotation.

    ``cameras.json`` stores the c2w pose as 16 column-major floats. ``as_pose``
    rebuilds that 4×4 (order F) before the axis is read.
    """
    return -as_pose(pose)[:3, 2]


def build_windows(frames: list[dict], params: DenseParams) -> list[list[int]]:
    """Windows of ``window`` frames every ``stride``, same-facing with the first.

    ``frames`` are dicts with ``epoch``, ``timestamp`` and ``pose`` (4×4 c2w).
    A window with fewer than 3 members after the facing cut is skipped.
    """
    order = sorted(
        range(len(frames)), key=lambda i: (str(frames[i].get("epoch", "")), float(frames[i].get("timestamp", i)))
    )
    windows: list[list[int]] = []
    i = 0
    while i < len(order):
        members = order[i : i + params.window]
        if len(members) < 3:
            break
        lead = camera_forward(frames[members[0]]["pose"])
        ln = np.linalg.norm(lead)
        kept = []
        for j in members:
            fwd = camera_forward(frames[j]["pose"])
            fn = np.linalg.norm(fwd)
            cos = float(lead @ fwd / max(ln * fn, 1e-9))
            if cos > params.same_facing_cos:
                kept.append(j)
        if len(kept) >= 3:
            windows.append(kept)
        i += params.stride
    return windows


def flag_window(rms_m: float, scale: float, median_scale: float, params: DenseParams) -> bool:
    if rms_m > params.window_rms_max_m:
        return True
    if median_scale <= 0:
        return True
    return abs(scale / median_scale - 1.0) > params.window_scale_dev_max
