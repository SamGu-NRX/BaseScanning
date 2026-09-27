# vendored from internal/modal-pipeline/housepipe/coverage.py (store, 2026-09-26)
"""Per-frame MoGe-2 depth and the §3.1 coverage rule with an occlusion test.

MoGe-2 ViT-S is the licence-safe per-frame model from A3 (Metric3D v2 is the
other allowed choice; MoGe-2 is what A3 already scored). A view counts toward
coverage only when the point is inside the frame, within 75 degrees of the
local surface normal, and not hidden behind the predicted depth by more than
max(0.10 m, 0.03 z).
"""

from __future__ import annotations

import math
import time

import numpy as np

MOGE_REPO = "Ruicheng/moge-2-vits-normal"
RELIEF_M = 0.10
RELIEF_REL = 0.03
MAX_INCIDENCE_DEG = 75.0


def load_moge(device: str = "cuda"):
    from moge.model.v2 import MoGeModel

    return MoGeModel.from_pretrained(MOGE_REPO).to(device).eval()


def predict_depth(model, rgb: np.ndarray, K: list[float]) -> tuple[np.ndarray, np.ndarray, float]:
    import torch

    h, w = rgb.shape[:2]
    fov_x = math.degrees(2 * math.atan(w / (2 * float(K[0]))))
    tensor = torch.from_numpy(rgb).permute(2, 0, 1).float().cuda() / 255.0
    t0 = time.perf_counter()
    with torch.inference_mode():
        out = model.infer(tensor, resolution_level=9, fov_x=fov_x, use_fp16=True)
    seconds = time.perf_counter() - t0
    depth = out["depth"].detach().float().cpu().numpy()
    mask = out["mask"].detach().cpu().numpy().astype(bool)
    return depth, mask, seconds


def _normals(points: np.ndarray) -> np.ndarray:
    """Outward-agnostic normals from a small neighbourhood. Sign is unused; incidence uses abs."""
    n = len(points)
    normals = np.tile(np.array([0.0, 1.0, 0.0]), (n, 1))
    if n < 8:
        return normals
    # Compare each sample with a fixed random subset instead of a full knn.
    rng = np.random.default_rng(0)
    subset = points[rng.choice(n, min(n, 64), replace=False)]
    for i in range(n):
        d = np.linalg.norm(subset - points[i], axis=1)
        nn = subset[np.argsort(d)[:8]]
        _, _, vt = np.linalg.svd(nn - nn.mean(0), full_matrices=False)
        normals[i] = vt[-1]
    return normals


def coverage_report(
    points: np.ndarray,
    cameras: list[dict],
    depths: dict[str, tuple[np.ndarray, np.ndarray]],
    *,
    max_points: int = 4000,
) -> dict:
    if len(points) == 0 or not cameras:
        return {"points": 0, "observed_share": 0.0, "grazing_share": 0.0, "unobserved_share": 1.0}
    rng = np.random.default_rng(0)
    if len(points) > max_points:
        points = points[rng.choice(len(points), max_points, replace=False)]
    normals = _normals(points)
    observed = np.zeros(len(points), np.int32)
    grazing = np.zeros(len(points), np.int32)
    for cam in cameras:
        T = cam["c2w"]
        K = cam["K_full"]
        fx, fy, cx, cy = K
        w, h = cam["w"], cam["h"]
        R = T[:3, :3]
        c = T[:3, 3]
        Xc = (points - c) @ R
        z = -Xc[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = fx * Xc[:, 0] / z + cx
            v = -fy * Xc[:, 1] / z + cy
        inside = (z > 0.3) & (u > 0.03 * w) & (u < 0.97 * w) & (v > 0.03 * h) & (v < 0.97 * h)
        view = points - c
        view /= np.clip(np.linalg.norm(view, axis=1, keepdims=True), 1e-8, None)
        cos = np.abs(np.sum(normals * view, axis=1))
        incidence = np.degrees(np.arccos(np.clip(cos, 0, 1)))
        frontal = inside & (incidence <= MAX_INCIDENCE_DEG)
        grazing_view = inside & (incidence > MAX_INCIDENCE_DEG)
        depth = depths.get(cam["name"])
        if depth is not None and frontal.any():
            zbuf, valid = depth
            ui = np.clip(u.astype(int), 0, w - 1)
            vi = np.clip(v.astype(int), 0, h - 1)
            # Depth maps follow the image size passed to MoGe (the packet image).
            if zbuf.shape != (h, w):
                import cv2

                zbuf = cv2.resize(zbuf, (w, h), interpolation=cv2.INTER_NEAREST)
                valid = cv2.resize(valid.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)
            pred = zbuf[vi, ui]
            ok = valid[vi, ui]
            hidden = ok & (pred + np.maximum(RELIEF_M, RELIEF_REL * z) < z)
            frontal = frontal & ~hidden
        observed += frontal.astype(np.int32)
        grazing += grazing_view.astype(np.int32)
    obs = observed >= 2
    graz = (~obs) & (grazing + observed >= 1)
    unobs = ~(obs | graz)
    return {
        "points": int(len(points)),
        "rule": {"min_views": 2, "max_incidence_deg": MAX_INCIDENCE_DEG, "relief_m": RELIEF_M},
        "observed_share": round(float(obs.mean()), 4),
        "grazing_share": round(float(graz.mean()), 4),
        "unobserved_share": round(float(unobs.mean()), 4),
        "median_views": float(np.median(observed)),
        "depth_model": MOGE_REPO,
    }
