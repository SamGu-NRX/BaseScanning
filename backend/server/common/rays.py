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
    """
    pts = np.asarray(points, float)
    src = np.asarray(src)
    out = []
    for cam_index, cam in enumerate(cameras):
        origins_rows = []
        for point_index, cams in enumerate(tracks):
            if cam_index in set(int(v) for v in np.asarray(cams).ravel()):
                origins_rows.append(point_index)
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
