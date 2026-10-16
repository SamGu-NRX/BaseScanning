"""Depth maps, flying-pixel filter, and per-frame scale to SfM tracks."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import maximum_filter, minimum_filter


@dataclass
class DepthMap:
    depth: np.ndarray  # (h/4, w/4) float32, NaN = none
    scale: float
    source: str  # lidar | moge2


def flying_pixel_mask(depth: np.ndarray, rel: float = 0.05) -> np.ndarray:
    """True where the 5×5 depth range is within ``rel`` of the pixel's depth."""
    z = np.asarray(depth, float)
    valid = np.isfinite(z) & (z > 1e-6)
    z_min = np.where(valid, z, 1e9)
    z_max = np.where(valid, z, 0.0)
    span = maximum_filter(z_max, size=5) - minimum_filter(z_min, size=5)
    return valid & (span <= rel * z)


def downsample_depth(depth: np.ndarray, factor: int = 4) -> np.ndarray:
    """Quarter resolution with a 3×3 minimum filter. NaN stays none."""
    z = np.asarray(depth, np.float32)
    h, w = z.shape
    zh, zw = max(1, h // factor), max(1, w // factor)
    cropped = z[: zh * factor, : zw * factor]
    blocks = cropped.reshape(zh, factor, zw, factor)
    with np.errstate(all="ignore"):
        small = np.nanmin(blocks, axis=(1, 3))
    small = minimum_filter(np.where(np.isfinite(small), small, 1e9), size=3)
    small = np.where(small > 1e8, np.nan, small).astype(np.float32)
    return small


def scale_depth_to_tracks(z_pred: np.ndarray, z_sfm: np.ndarray, min_points: int = 20) -> float | None:
    """Median ``z_sfm / z_pred``. None when fewer than ``min_points`` finite ratios."""
    pred = np.asarray(z_pred, float)
    sfm = np.asarray(z_sfm, float)
    ok = np.isfinite(pred) & np.isfinite(sfm) & (pred > 1e-4) & (sfm > 1e-4)
    if int(ok.sum()) < min_points:
        return None
    return float(np.median(sfm[ok] / pred[ok]))
