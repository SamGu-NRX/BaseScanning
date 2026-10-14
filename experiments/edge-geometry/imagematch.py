"""Image operations for M2 and M3: snapping a tap to a vertical edge, and matching a pixel along
its epipolar segment in a second photo. Pixels are OpenCV integer-centred; poses are 4x4
camera-to-world with OpenCV camera axes."""

from __future__ import annotations

import cv2
import numpy as np


def vertical_edge_strength(gray: np.ndarray, rows: int = 9) -> np.ndarray:
    """|dI/dx| averaged over `rows` rows, so a vertical edge scores along its whole run and a
    horizontal one scores nothing."""
    gx = cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    return cv2.blur(np.abs(gx), (1, rows), borderType=cv2.BORDER_REFLECT)


def _parabola_offset(left: float, mid: float, right: float) -> float:
    """Sub-sample offset of a peak from three samples, clipped to half a sample."""
    denom = left - 2.0 * mid + right
    if denom >= 0 or not np.isfinite(denom):
        return 0.0
    return float(np.clip(0.5 * (left - right) / denom, -0.5, 0.5))


def snap(strength: np.ndarray, u: float, v: float, radius: int = 25) -> tuple[float, float]:
    """Move a tap horizontally to the strongest vertical edge within `radius` px on its row,
    refined to sub-pixel by a parabola. A tap with no gradient in reach stays where it is."""
    h, w = strength.shape
    row = int(np.clip(round(v), 0, h - 1))
    lo = int(np.clip(round(u) - radius, 0, w - 1))
    hi = int(np.clip(round(u) + radius, 0, w - 1))
    line = strength[row, lo : hi + 1]
    if not len(line) or line.max() <= 0:
        return float(u), float(v)
    i = int(np.argmax(line))
    off = 0.0
    if 0 < i < len(line) - 1:
        off = _parabola_offset(float(line[i - 1]), float(line[i]), float(line[i + 1]))
    return float(lo + i + off), float(v)


def pixel_ray(K: np.ndarray, T: np.ndarray, uv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """World origin and direction of the ray through pixel uv; the direction has unit optical-axis
    depth, so origin + z * direction is the point at depth z."""
    d_cam = np.array([(uv[0] - K[0, 2]) / K[0, 0], (uv[1] - K[1, 2]) / K[1, 1], 1.0])
    return T[:3, 3].copy(), T[:3, :3] @ d_cam


def project(K: np.ndarray, T: np.ndarray, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pixels (N, 2) and camera depths (N,) of world points (N, 3)."""
    Xc = (np.atleast_2d(X) - T[:3, 3]) @ T[:3, :3]
    with np.errstate(divide="ignore", invalid="ignore"):
        uv = np.c_[K[0, 0] * Xc[:, 0] / Xc[:, 2] + K[0, 2], K[1, 1] * Xc[:, 1] / Xc[:, 2] + K[1, 2]]
    return uv, Xc[:, 2]


def _ncc(template: np.ndarray, patches: np.ndarray) -> np.ndarray:
    t = template.ravel() - template.mean()
    p = patches.reshape(len(patches), -1)
    p = p - p.mean(axis=1, keepdims=True)
    denom = np.linalg.norm(t) * np.linalg.norm(p, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (p @ t) / denom
    return np.where(denom > 1e-9, out, -1.0)


def epipolar_match(
    img1: np.ndarray,
    img2: np.ndarray,
    uv1: np.ndarray,
    K1: np.ndarray,
    T1: np.ndarray,
    K2: np.ndarray,
    T2: np.ndarray,
    depth_range: tuple[float, float] = (0.5, 10.0),
    patch: int = 15,
    step_px: float = 0.5,
) -> tuple[np.ndarray, float]:
    """Best match in img2 for the `patch` x `patch` window around uv1 in img1, by normalised
    cross-correlation along the segment of uv1's epipolar line that depths in `depth_range` map
    to, sampled every `step_px` and refined by a parabola. Returns (uv2, peak NCC); uv2 is NaN
    and NCC -1 when no part of the segment fits in img2 or the template leaves img1."""
    half = patch // 2
    h1, w1 = img1.shape
    h2, w2 = img2.shape
    fail = (np.full(2, np.nan), -1.0)
    if not (half <= uv1[0] <= w1 - 1 - half and half <= uv1[1] <= h1 - 1 - half):
        return fail
    template = cv2.getRectSubPix(img1.astype(np.float32), (patch, patch), (float(uv1[0]), float(uv1[1])))

    # The depths whose projection lands inside img2, in front of it: an interval along the ray,
    # so its two ends bound a straight segment in img2.
    o, dvec = pixel_ray(K1, T1, uv1)
    z = np.geomspace(depth_range[0], depth_range[1], 512)
    uv, zc = project(K2, T2, o + z[:, None] * dvec)
    inside = (
        (zc > 0.05)
        & (uv[:, 0] >= half)
        & (uv[:, 0] <= w2 - 1 - half)
        & (uv[:, 1] >= half)
        & (uv[:, 1] <= h2 - 1 - half)
    )
    if not inside.any():
        return fail
    ends = uv[np.flatnonzero(inside)[[0, -1]]]
    length = float(np.linalg.norm(ends[1] - ends[0]))
    n = max(3, int(np.ceil(length / step_px)) + 1)
    samples = ends[0] + np.linspace(0.0, 1.0, n)[:, None] * (ends[1] - ends[0])

    offs = np.arange(patch, dtype=np.float32) - half
    image2 = img2.astype(np.float32)
    # cv2.remap takes at most SHRT_MAX rows, and a wide photo pair's segment can cross most of
    # img2, so the patches are cut in chunks that fit; shorter segments are one chunk, as before.
    chunk = (np.iinfo(np.int16).max - 1) // patch
    patches = []
    for a in range(0, n, chunk):
        part = samples[a : a + chunk]
        m = len(part)
        map_x = (part[:, 0, None, None] + offs[None, None, :]).astype(np.float32)
        map_y = (part[:, 1, None, None] + offs[None, :, None]).astype(np.float32)
        map_x = np.broadcast_to(map_x, (m, patch, patch)).reshape(m * patch, patch)
        map_y = np.broadcast_to(map_y, (m, patch, patch)).reshape(m * patch, patch)
        patches.append(cv2.remap(image2, map_x, map_y, cv2.INTER_LINEAR).reshape(m, patch, patch))
    score = _ncc(template, np.concatenate(patches))
    i = int(np.argmax(score))
    off = 0.0
    if 0 < i < n - 1:
        off = _parabola_offset(float(score[i - 1]), float(score[i]), float(score[i + 1]))
    step = (ends[1] - ends[0]) / (n - 1)
    return samples[i] + off * step, float(score[i])
