"""Per-photo scale factors for predicted depth, from features triangulated with given poses.

Learned metric depth gets each photo's scale wrong by a few percent, differently per photo. Poses
from a phone's AR tracking are metric (with their own error). This module matches SIFT features
across a group of photos, triangulates each match with the given poses and intrinsics, and fits
one factor per photo that makes its predicted depth agree with the triangulated depths:
scale = median(z_tri / z_pred) over the points that photo sees. Multiplying the photo's depth map
by `scale` puts it on the poses' metric.

Conventions: OpenCV cameras (+x right, +y down, +z forward), pixel centres at integers, poses as
4x4 camera-to-world in meters, depth as z along the camera axis.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import cv2
import numpy as np

MAX_FEATURES = 4000
RATIO = 0.8
MAD_TO_SIGMA = 1.4826  # MAD of a normal distribution times this is its standard deviation


@dataclass(frozen=True)
class ScaleFit:
    scale: float | None  # multiply the photo's predicted depth by this; None if too few points
    points: int  # triangulated points used for this photo
    spread: float | None  # 1.4826 * MAD of log(z_tri / z_pred) over those points (dimensionless)


@dataclass(frozen=True)
class PairPoints:
    X: np.ndarray  # (N, 3) world points
    keep: np.ndarray  # (N,) bool: in front of both cameras, reprojects, wide enough angle


def sample_depth(depth: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Bilinear depth at float pixel positions (OpenCV integer-centred). NaN where any of the four
    neighbours is NaN, non-positive or outside the image: blending across a hole or an edge of
    valid depth would invent a value."""
    d = np.where(np.isfinite(depth) & (depth > 0), depth, np.nan).astype(np.float64)
    h, w = d.shape
    x, y = uv[:, 0], uv[:, 1]
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = x - x0, y - y0
    out = np.full(len(uv), np.nan)
    ok = (x0 >= 0) & (y0 >= 0) & (x0 + 1 < w) & (y0 + 1 < h)
    x0, y0, fx, fy = x0[ok], y0[ok], fx[ok], fy[ok]
    out[ok] = (
        d[y0, x0] * (1 - fx) * (1 - fy)
        + d[y0, x0 + 1] * fx * (1 - fy)
        + d[y0 + 1, x0] * (1 - fx) * fy
        + d[y0 + 1, x0 + 1] * fx * fy
    )
    return out


def projection(K: np.ndarray, cam_to_world: np.ndarray) -> np.ndarray:
    """3x4 P = K [R | t] with R, t world-to-camera."""
    R = cam_to_world[:3, :3].T
    t = -R @ cam_to_world[:3, 3]
    return K @ np.c_[R, t]


def triangulate(P1: np.ndarray, P2: np.ndarray, x1: np.ndarray, x2: np.ndarray) -> np.ndarray:
    """Linear (DLT) triangulation of (N, 2) pixel correspondences into (N, 3) world points.

    Each view gives two rows of A X = 0 (u P[2] - P[0], v P[2] - P[1]); X is A's right singular
    vector with the smallest singular value. Rows are normalised to unit length so pixel units do
    not weight one view over the other. A point at infinity comes back as inf or NaN.
    """
    A = np.stack(
        [
            x1[:, :1] * P1[2] - P1[0],
            x1[:, 1:2] * P1[2] - P1[1],
            x2[:, :1] * P2[2] - P2[0],
            x2[:, 1:2] * P2[2] - P2[1],
        ],
        axis=1,
    )
    A /= np.linalg.norm(A, axis=2, keepdims=True)
    Xh = np.linalg.svd(A)[2][:, -1]
    with np.errstate(divide="ignore", invalid="ignore"):
        return Xh[:, :3] / Xh[:, 3:]


def triangulate_pair(
    K1: np.ndarray,
    T1: np.ndarray,
    K2: np.ndarray,
    T2: np.ndarray,
    x1: np.ndarray,
    x2: np.ndarray,
    max_reproj_px: float,
    min_angle_deg: float,
) -> PairPoints:
    """Triangulate correspondences between two posed cameras and flag the trustworthy ones: in
    front of both cameras, reprojecting within `max_reproj_px` in both, and seen along rays at
    least `min_angle_deg` apart (with a tiny baseline, a sub-pixel error moves depth a lot)."""
    P1, P2 = projection(K1, T1), projection(K2, T2)
    X = triangulate(P1, P2, x1, x2)
    keep = np.isfinite(X).all(axis=1)
    Xh = np.c_[X, np.ones(len(X))]
    for P, x in ((P1, x1), (P2, x2)):
        p = Xh @ P.T
        with np.errstate(divide="ignore", invalid="ignore"):
            err = np.linalg.norm(p[:, :2] / p[:, 2:] - x, axis=1)
        # K has a positive last row, so p's third coordinate has the sign of the camera-frame z.
        keep &= (p[:, 2] > 0) & (err <= max_reproj_px)
    r1 = X - T1[:3, 3]
    r2 = X - T2[:3, 3]
    with np.errstate(divide="ignore", invalid="ignore"):
        cos = np.sum(r1 * r2, axis=1) / (np.linalg.norm(r1, axis=1) * np.linalg.norm(r2, axis=1))
    keep &= cos <= np.cos(np.radians(min_angle_deg))
    return PairPoints(X=X, keep=keep)


def match(desc1: np.ndarray, desc2: np.ndarray, ratio: float = RATIO) -> np.ndarray:
    """(M, 2) index pairs (into desc1, desc2): Lowe's ratio test from 1 to 2, and 1's best match
    in 2 must pick 1 back as its best match in 1 (mutual)."""
    if len(desc1) < 2 or len(desc2) < 2:
        return np.empty((0, 2), np.int64)
    bf = cv2.BFMatcher(cv2.NORM_L2)
    fwd = bf.knnMatch(desc1, desc2, k=2)
    back = np.array([m[0].trainIdx for m in bf.knnMatch(desc2, desc1, k=1)])
    out = [
        (m[0].queryIdx, m[0].trainIdx)
        for m in fwd
        if len(m) == 2
        and m[0].distance < ratio * m[1].distance
        and back[m[0].trainIdx] == m[0].queryIdx
    ]
    return np.array(out, np.int64).reshape(-1, 2)


def _check_inputs(images, K, cam_to_world, depth) -> None:
    names = set(images)
    for label, d in (("K", K), ("cam_to_world", cam_to_world), ("depth", depth)):
        if set(d) != names:
            raise ValueError(
                f"{label} names {sorted(d)} do not match images {sorted(names)}: every photo "
                "needs an image, intrinsics, a pose and a depth map"
            )
    if len(names) < 2:
        raise ValueError(f"need at least 2 photos to triangulate, got {len(names)}")
    for n in names:
        img = images[n]
        if img.ndim != 2 or img.dtype != np.uint8:
            raise ValueError(f"{n}: image must be grayscale uint8 HxW, got {img.dtype} {img.shape}")
        if depth[n].shape != img.shape:
            raise ValueError(f"{n}: depth {depth[n].shape} != image {img.shape}")
        if K[n].shape != (3, 3):
            raise ValueError(f"{n}: K must be 3x3, got {K[n].shape}")
        T = cam_to_world[n]
        if T.shape != (4, 4):
            raise ValueError(f"{n}: cam_to_world must be 4x4, got {T.shape}")
        R = T[:3, :3]
        if not np.allclose(R.T @ R, np.eye(3), atol=1e-6) or np.linalg.det(R) < 0:
            raise ValueError(f"{n}: cam_to_world rotation is not a proper rotation")


def view_scales(
    images: dict[str, np.ndarray],
    K: dict[str, np.ndarray],
    cam_to_world: dict[str, np.ndarray],
    depth: dict[str, np.ndarray],
    min_points: int = 20,
    max_reproj_px: float = 2.0,
    min_angle_deg: float = 2.0,
) -> dict[str, ScaleFit]:
    """{photo: ScaleFit} for a group of posed photos with predicted depth.

    Every pair of photos is matched and triangulated. A kept point counts once for each photo of
    its pair: z_tri is its depth in that photo's camera, z_pred the predicted depth bilinearly
    sampled at that photo's keypoint (skipped where invalid). The median ratio is robust to the
    mismatches that survive the filters.
    """
    _check_inputs(images, K, cam_to_world, depth)
    sift = cv2.SIFT_create(nfeatures=MAX_FEATURES)
    feats = {}
    for n, img in images.items():
        kps, desc = sift.detectAndCompute(img, None)
        uv = np.array([kp.pt for kp in kps], np.float64).reshape(-1, 2)
        feats[n] = (uv, np.zeros((0, 128), np.float32) if desc is None else desc)

    ratios: dict[str, list[np.ndarray]] = {n: [] for n in images}
    for a, b in combinations(sorted(images), 2):
        idx = match(feats[a][1], feats[b][1])
        if len(idx) == 0:
            continue
        xa, xb = feats[a][0][idx[:, 0]], feats[b][0][idx[:, 1]]
        pts = triangulate_pair(
            K[a], cam_to_world[a], K[b], cam_to_world[b], xa, xb, max_reproj_px, min_angle_deg
        )
        X = pts.X[pts.keep]
        for n, x in ((a, xa[pts.keep]), (b, xb[pts.keep])):
            T = cam_to_world[n]
            z_tri = (X - T[:3, 3]) @ T[:3, 2]  # camera z axis in world = third column of R_cw
            z_pred = sample_depth(depth[n], x)
            ok = np.isfinite(z_pred)
            ratios[n].append(z_tri[ok] / z_pred[ok])

    out = {}
    for n in sorted(images):
        r = np.concatenate(ratios[n]) if ratios[n] else np.empty(0)
        if len(r) < min_points:
            out[n] = ScaleFit(scale=None, points=len(r), spread=None)
            continue
        log_r = np.log(r)
        spread = MAD_TO_SIGMA * float(np.median(np.abs(log_r - np.median(log_r))))
        out[n] = ScaleFit(scale=float(np.median(r)), points=len(r), spread=spread)
    return out
