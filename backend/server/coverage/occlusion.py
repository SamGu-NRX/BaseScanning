"""MoGe-style depth prediction hook and the relief test used by the query.

SD-61 lives in ``server.common.occluders``: a missing laser or recon PLY is an
error on every scoring path. ``--allow-missing-occluders`` is debug-only.
"""

from __future__ import annotations

import numpy as np

from server.common.occluders import (
    MissingOccluderGeometry,
    check_occluder_geometry,
    gate_rejects_provisional,
    missing_occluder_files,
)

__all__ = [
    "MissingOccluderGeometry",
    "check_occluder_geometry",
    "depth_hidden",
    "gate_rejects_provisional",
    "missing_occluder_files",
    "predict_depth",
]


def depth_hidden(z_surface: float, z_depth: float, relief_m: float = 0.10, relief_rel: float = 0.04) -> bool:
    if not np.isfinite(z_depth):
        return True
    return z_depth + max(relief_m, relief_rel * z_surface) < z_surface


def predict_depth(model, rgb: np.ndarray, k: np.ndarray) -> np.ndarray:
    """Call a depth model. Tests pass a callable; weights are not loaded here."""
    if callable(model):
        return np.asarray(model(rgb, k), np.float32)
    raise RuntimeError("depth model is not loaded (set HSS_DENSE_WEIGHTS or pass a callable)")
