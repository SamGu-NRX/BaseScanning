"""Mapper frame budget (SD-10). Leftovers are registered later, not mapped again."""

from __future__ import annotations

import math

import numpy as np

from server.poses.params import PoseParams


def heading_deg(pose: np.ndarray) -> float:
    """Yaw of the ARKit camera's forward axis (−Z), degrees, y up."""
    forward = -np.asarray(pose, float)[:3, 2]
    return math.degrees(math.atan2(float(forward[0]), float(forward[2])))


def _angdiff(a: float, b: float) -> float:
    d = (a - b) % 360.0
    if d > 180.0:
        d -= 360.0
    return abs(d)


def _distinct(i: int, selected: list[int], centers: np.ndarray, headings: list[float]) -> bool:
    for j in selected:
        if float(np.linalg.norm(centers[i] - centers[j])) > 0.5:
            continue
        if _angdiff(headings[i], headings[j]) <= 20.0:
            return False
    return True


def select_guided(
    n: int,
    centers: np.ndarray,
    headings: list[float],
    *,
    must: list[int],
    limit: int,
) -> list[int]:
    """Meter and close-ups first, then farthest-point on centres."""
    if n <= limit:
        return list(range(n))
    selected: list[int] = []
    for i in must:
        if 0 <= i < n and i not in selected:
            selected.append(i)
    remaining = [i for i in range(n) if i not in selected]
    while len(selected) < limit and remaining:
        pool = [i for i in remaining if _distinct(i, selected, centers, headings)] or remaining
        if not selected:
            pick = pool[0]
        else:

            def nearest(i: int) -> float:
                return min(float(np.linalg.norm(centers[i] - centers[j])) for j in selected)

            pick = max(pool, key=nearest)
        selected.append(pick)
        remaining.remove(pick)
    return selected


def select_photo(n: int, *, must: list[int], limit: int) -> list[int]:
    """Every k-th frame in capture order. Close-ups are always kept."""
    if n <= limit:
        return list(range(n))
    k = math.ceil(n / limit)
    chosen = list(range(0, n, k))
    for i in must:
        if 0 <= i < n and i not in chosen:
            chosen.append(i)
    if len(chosen) > limit:
        # Keep must-frames, then the most even subset of the rest.
        forced = [i for i in chosen if i in must]
        rest = [i for i in chosen if i not in forced]
        room = max(limit - len(forced), 0)
        if room == 0:
            return forced[:limit]
        step = max(len(rest) / room, 1.0)
        picked = [rest[min(int(i * step), len(rest) - 1)] for i in range(room)]
        chosen = forced + picked
    return sorted(set(chosen))


def budget_limit(params: PoseParams) -> int:
    return max(int(params.sfm_max_frames), 2)
