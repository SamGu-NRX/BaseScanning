"""Pair lists. Guided: sequential overlap plus spatial neighbours inside one epoch.

The radius is a hard cap. Housepipe's ``spatial_pairs`` also keeps neighbours
past the radius once four are found; that path is not used (SD-35).
"""

from __future__ import annotations

import numpy as np

from server.poses.params import PoseParams


def _add(pairs: set[tuple[str, str]], a: str, b: str) -> None:
    if a == b:
        return
    pairs.add((a, b) if a < b else (b, a))


def sequential_pairs(names: list[str], overlap: int) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for i in range(len(names)):
        for j in range(i + 1, min(len(names), i + 1 + overlap)):
            _add(pairs, names[i], names[j])
    return sorted(pairs)


def spatial_pairs(
    names: list[str],
    centers: np.ndarray,
    *,
    max_neighbors: int,
    radius_m: float,
) -> list[tuple[str, str]]:
    """Up to ``max_neighbors`` other centres within ``radius_m`` of each frame."""
    centers = np.asarray(centers, float)
    pairs: set[tuple[str, str]] = set()
    n = len(names)
    for i in range(n):
        dist = np.linalg.norm(centers - centers[i], axis=1)
        order = np.argsort(dist, kind="mergesort")
        kept = 0
        for j in order:
            j = int(j)
            if j == i:
                continue
            if dist[j] > radius_m:
                break
            _add(pairs, names[i], names[j])
            kept += 1
            if kept >= max_neighbors:
                break
    return sorted(pairs)


def guided_pairs(
    names: list[str],
    centers: np.ndarray,
    params: PoseParams,
) -> list[tuple[str, str]]:
    pairs = set(sequential_pairs(names, params.sequential_overlap))
    pairs.update(
        spatial_pairs(
            names,
            centers,
            max_neighbors=params.spatial_neighbors,
            radius_m=params.spatial_radius_m,
        )
    )
    return sorted(pairs)


def cross_epoch_pairs(
    left: list[str],
    right: list[str],
    overlap: int,
    *,
    exhaustive_cap: int = 20_000,
) -> list[tuple[str, str]]:
    """Pairs across an epoch boundary.

    ``left`` and ``right`` are in capture order. The boundary window is the last
    ``overlap`` names of ``left`` and the first ``overlap`` of ``right``. When
    the product of the two epochs is at most ``exhaustive_cap``, every cross
    pair is included.
    """
    pairs: set[tuple[str, str]] = set()
    if left and right and len(left) * len(right) <= exhaustive_cap:
        for a in left:
            for b in right:
                _add(pairs, a, b)
        return sorted(pairs)
    edge_l = left[-overlap:] if overlap > 0 else []
    edge_r = right[:overlap] if overlap > 0 else []
    for a in edge_l:
        for b in edge_r:
            _add(pairs, a, b)
    return sorted(pairs)


def exhaustive_pairs(names: list[str]) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            _add(pairs, names[i], names[j])
    return sorted(pairs)


def photo_pairs(
    names: list[str],
    centers: np.ndarray | None,
    params: PoseParams,
) -> tuple[list[tuple[str, str]], bool]:
    """Image-only pairs. Returns ``(pairs, exhaustive)``.

    Exhaustive up to ``exhaustive_max_images``. Above that, sequential overlap
    in capture order, plus spatial pairs when centres exist (A10: spacing, not
    a 3-neighbour window).
    """
    if len(names) <= params.exhaustive_max_images:
        return exhaustive_pairs(names), True
    pairs = set(sequential_pairs(names, params.sequential_overlap))
    if centers is not None:
        pairs.update(
            spatial_pairs(
                names,
                centers,
                max_neighbors=params.spatial_neighbors,
                radius_m=params.spatial_radius_m,
            )
        )
    return sorted(pairs), False
