"""Ray sets for the coverage grid. Flagged windows contribute nothing."""

from __future__ import annotations

import numpy as np


def rays_from_tracks(
    cameras: list,
    points: np.ndarray,
    tracks: list[np.ndarray],
    src: np.ndarray,
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray | None]]:
    """One ``(origin, ends, end_is_surface)`` per camera.

    Sparse tracks use ``end_is_surface=None``. LiDAR (src 2) and π³ (src 1) ends
    are surfaces. ``src`` aligns with ``points``.

    Camera membership is inverted once. An empty track list is one pass over the
    points, not one pass per camera: the coverage cloud is hundreds of thousands
    of points and the old nested scan blew the L4 coverage cap.
    """
    pts = np.asarray(points, float)
    src = np.asarray(src)
    buckets: list[list[int]] = [[] for _ in cameras]
    n_cams = len(cameras)
    for point_index, cams in enumerate(tracks):
        seen = np.asarray(cams).ravel()
        if seen.size == 0:
            continue
        for cam_index in dict.fromkeys(int(v) for v in seen):
            if 0 <= cam_index < n_cams:
                buckets[cam_index].append(point_index)
    out = []
    for cam_index, cam in enumerate(cameras):
        origins_rows = buckets[cam_index]
        if not origins_rows:
            out.append((cam.pose[:3, 3], np.zeros((0, 3)), None))
            continue
        idx = np.asarray(origins_rows, int)
        ends = pts[idx]
        kinds = src[idx]
        if np.all(kinds == 0):
            surface = None
        else:
            surface = kinds != 0
        out.append((cam.pose[:3, 3], ends, surface))
    return out
