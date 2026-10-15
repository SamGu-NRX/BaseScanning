"""Meter close-up registration check. WS-05 decides scale; we only report."""

from __future__ import annotations

from server.poses.params import PoseParams


def closeup_registered(
    *,
    in_primary: bool,
    points3d: int,
    reproj_px: float | None,
    params: PoseParams,
) -> bool:
    if not in_primary or reproj_px is None:
        return False
    return points3d >= params.closeup_min_points3d and reproj_px <= params.closeup_max_reproj_px


def join_accepted(*, n_registered: int, mean_reproj_px: float, rms_m: float) -> bool:
    return n_registered >= 3 and mean_reproj_px <= 2.0 and rms_m <= 0.10
