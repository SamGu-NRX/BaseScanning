"""Edge-snapped measurement endpoints from rough hand annotation.

An annotated measurement is two roughly parallel image edges `a` and `b`, each given by two rough points.
Each edge is snapped to the strongest intensity step within +-R px along its normal (sub-pixel, over the
middle 70 % of the edge) and re-fitted as a line (RANSAC, 1.5 px). The measurement endpoints are where the
line through the centre of the region, along edge a's normal, crosses the two refined lines.
"""

from __future__ import annotations

import cv2
import numpy as np

from common import image_path

_CACHE: dict = {}


def gray(scene: str, stem: str) -> np.ndarray:
    k = (scene, stem)
    if k not in _CACHE:
        g = cv2.imread(str(image_path(scene, stem)), cv2.IMREAD_GRAYSCALE).astype(np.float32)
        _CACHE.clear()
        _CACHE[k] = cv2.GaussianBlur(g, (0, 0), 1.0)
    return _CACHE[k]


def sample(img: np.ndarray, pts: np.ndarray) -> np.ndarray:
    m = pts.reshape(-1, 1, 2).astype(np.float32)
    return cv2.remap(img, m[..., 0], m[..., 1], cv2.INTER_LINEAR).ravel()


def snap_edge(img: np.ndarray, p, q, R: float = 12.0, n_samples: int = 40, sign: int = 0):
    p, q = np.asarray(p, float), np.asarray(q, float)
    u = (q - p) / np.linalg.norm(q - p)
    n = np.array([-u[1], u[0]])
    ts = np.linspace(0.15, 0.85, n_samples)
    offs = np.arange(-R, R + 0.5, 0.5)
    hits, strengths = [], []
    for t in ts:
        c = p + t * (q - p)
        prof = sample(img, c + offs[:, None] * n)
        g = np.gradient(prof)
        if sign:
            g = g * sign
        else:
            g = np.abs(g)
        k = int(np.argmax(g))
        if 0 < k < len(g) - 1:
            a, b, cc = g[k - 1], g[k], g[k + 1]
            den = a - 2 * b + cc
            dk = 0.5 * (a - cc) / den if den != 0 else 0.0
        else:
            dk = 0.0
        hits.append(c + (offs[k] + dk * 0.5) * n)
        strengths.append(g[k])
    H = np.array(hits)
    best, rng = None, np.random.default_rng(0)
    for _ in range(200):
        i, j = rng.choice(len(H), 2, replace=False)
        d = H[j] - H[i]
        if np.linalg.norm(d) < 1e-6:
            continue
        d /= np.linalg.norm(d)
        nn = np.array([-d[1], d[0]])
        inl = np.abs((H - H[i]) @ nn) < 1.5
        if best is None or inl.sum() > best.sum():
            best = inl
    Q = H[best]
    c = Q.mean(0)
    d = np.linalg.svd(Q - c, full_matrices=False)[2][0]
    return {"point": c, "dir": d, "inlier_frac": float(best.mean()), "strength": float(np.median(np.array(strengths)[best]))}


def intersect(c, v, line) -> np.ndarray:
    """Point where c + s v meets the line (point, dir)."""
    p, d = line["point"], line["dir"]
    A = np.c_[v, -d]
    s, _ = np.linalg.solve(A, p - c)
    return c + s * v


def endpoints(scene: str, stem: str, a, b, R: float = 12.0, raw: bool = False):
    """a, b: [[x, y], [x, y]] rough edges. Returns (A_uv, B_uv, qa, qb)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if raw:
        la = {"point": a.mean(0), "dir": (a[1] - a[0]) / np.linalg.norm(a[1] - a[0]), "inlier_frac": 1.0, "strength": 0.0}
        lb = {"point": b.mean(0), "dir": (b[1] - b[0]) / np.linalg.norm(b[1] - b[0]), "inlier_frac": 1.0, "strength": 0.0}
    else:
        img = gray(scene, stem)
        la, lb = snap_edge(img, a[0], a[1], R), snap_edge(img, b[0], b[1], R)
    c = (a.mean(0) + b.mean(0)) / 2
    v = np.array([-la["dir"][1], la["dir"][0]])
    return intersect(c, v, la), intersect(c, v, lb), la, lb


def overlay(scene: str, stem: str, items, out: str, pad: int = 60, width: int = 1000) -> None:
    """items: list of (A, B, la, lb, label). Draws refined lines and the measured segment."""
    img = cv2.imread(str(image_path(scene, stem)))
    pts = np.array([p for it in items for p in (it[0], it[1])])
    x0, y0 = np.maximum(pts.min(0) - pad, 0).astype(int)
    x1, y1 = np.minimum(pts.max(0) + pad, [img.shape[1], img.shape[0]]).astype(int)
    for A, B, la, lb, label in items:
        for ln in (la, lb):
            p, d = ln["point"], ln["dir"]
            cv2.line(img, tuple((p - 400 * d).astype(int)), tuple((p + 400 * d).astype(int)), (0, 255, 0), 1)
        cv2.line(img, tuple(A.astype(int)), tuple(B.astype(int)), (0, 0, 255), 1)
        cv2.putText(img, label, tuple((A + 5).astype(int)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    crop = img[y0:y1, x0:x1]
    s = width / crop.shape[1]
    cv2.imwrite(out, cv2.resize(crop, None, fx=s, fy=s), [cv2.IMWRITE_JPEG_QUALITY, 90])
