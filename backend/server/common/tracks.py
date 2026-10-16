"""SfM tracks aligned with a point cloud.

WS-04 writes ``poses/tracks.npz`` as CSR arrays ``indptr``, ``indices`` (camera
indices into ``cameras.json``), and ``point_ids`` in ``sparse.ply`` row order.
Dense keeps that order on the sparse suffix and writes the same CSR next to
the cloud so coverage does not invent a camera for each point.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def write_tracks(path: Path, tracks: list[np.ndarray], point_ids: np.ndarray) -> None:
    rows = [np.asarray(row, np.int32).ravel() for row in tracks]
    indptr = np.zeros(len(rows) + 1, np.int64)
    for i, row in enumerate(rows):
        indptr[i + 1] = indptr[i] + row.size
    if rows and indptr[-1]:
        indices = np.concatenate(rows).astype(np.int32, copy=False)
    else:
        indices = np.zeros(0, np.int32)
    np.savez_compressed(
        path,
        indptr=indptr,
        indices=indices,
        point_ids=np.asarray(point_ids, np.int64),
    )


def read_tracks(path: Path) -> tuple[list[np.ndarray], np.ndarray] | None:
    path = Path(path)
    if not path.is_file():
        return None
    data = np.load(path)
    if "indptr" not in data.files or "indices" not in data.files:
        return None
    indptr = np.asarray(data["indptr"], np.int64)
    indices = np.asarray(data["indices"], np.int32)
    if indptr.size < 1:
        return None
    n = int(indptr.size - 1)
    tracks = [indices[int(indptr[i]) : int(indptr[i + 1])].astype(np.int32, copy=True) for i in range(n)]
    if "point_ids" in data.files and len(data["point_ids"]) == n:
        ids = np.asarray(data["point_ids"], np.int64)
    else:
        ids = np.full(n, -1, np.int64)
    return tracks, ids


def empty_tracks(n: int) -> list[np.ndarray]:
    return [np.zeros(0, np.int32) for _ in range(n)]


def resolve_tracks(models: dict, dense_dir: Path, n_points: int) -> list[np.ndarray]:
    """Tracks aligned with the dense cloud. Never invents a camera index."""
    given = models.get("tracks")
    if given is not None and len(given) == n_points:
        return [np.asarray(row, np.int32).ravel() for row in given]
    loaded = read_tracks(Path(dense_dir) / "tracks.npz")
    if loaded is not None and len(loaded[0]) == n_points:
        return loaded[0]
    return empty_tracks(n_points)
