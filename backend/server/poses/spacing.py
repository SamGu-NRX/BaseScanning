"""A10: spreading photos along a walk breaks SfM; the count does not.

Warn ``photo_spacing_sparse`` when the registered share is under 0.60 or more
than one model comes back (brief §4.5). The same warning covers the §8.1 case:
40 frames thinned to about 1 m steps, a path longer than 20 m whose median gap
is above 0.9 m. ``sparse_photo_set`` is retired.
"""

from __future__ import annotations

import numpy as np

from server.poses.params import PoseParams


def photo_spacing(centers_in_order: np.ndarray | None) -> dict[str, float]:
    if centers_in_order is None or len(centers_in_order) < 2:
        return {"median_step_m": 0.0, "p90_step_m": 0.0, "sides_registered": 0.0}
    gaps = np.linalg.norm(np.diff(np.asarray(centers_in_order, float), axis=0), axis=1)
    return {
        "median_step_m": float(np.median(gaps)),
        "p90_step_m": float(np.quantile(gaps, 0.9)),
        "sides_registered": 1.0,
    }


def spacing_warnings(
    centers_in_order: np.ndarray | None,
    *,
    registered: int,
    given: int,
    n_models: int,
    params: PoseParams,
    metric_scale: bool = True,
) -> list[str]:
    """Warn on a thin registration or on a wide path.

    ``metric_scale`` is false for an image-only model, whose step lengths are
    in model units. The 0.9 m / 20 m thresholds apply only once a metric scale
    exists. The share and model-count checks always apply.
    """
    share = registered / given if given else 0.0
    wide = False
    if metric_scale and centers_in_order is not None and len(centers_in_order) >= 2:
        gaps = np.linalg.norm(np.diff(np.asarray(centers_in_order, float), axis=0), axis=1)
        path = float(gaps.sum())
        wide = path > params.spacing_path_m and float(np.median(gaps)) > params.spacing_gap_m
    if share < 0.60 or n_models > 1 or wide:
        return ["photo_spacing_sparse"]
    return []
