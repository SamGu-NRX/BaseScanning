"""build_grid marks the same voxels when ray lengths differ inside one camera."""

from __future__ import annotations

import numpy as np

from server.eval.coverage import build_grid
from server.eval.sceneio import CoverageGrid


def _reference(origin, ends, lo, hi, voxel):
    """One ray at a time, same sample rule as build_grid."""
    step = voxel * 0.5
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    shape = tuple(int(v) for v in np.ceil((hi - lo) / voxel))
    n = int(np.prod(shape))
    free = np.zeros(n, bool)
    occ = np.zeros(n, bool)
    dims = np.array(shape)

    def lin(points):
        ijk = np.floor((np.asarray(points, float) - lo) / voxel).astype(np.int64)
        ok = np.all((ijk >= 0) & (ijk < dims), axis=1)
        ijk = ijk[ok]
        if len(ijk) == 0:
            return np.zeros(0, np.int64)
        return (ijk[:, 0] * shape[1] + ijk[:, 1]) * shape[2] + ijk[:, 2]

    origin = np.asarray(origin, float)
    ends = np.asarray(ends, float)
    lengths = np.linalg.norm(ends - origin, axis=1)
    dirs = (ends - origin) / np.maximum(lengths, 1e-9)[:, None]
    for index in range(len(ends)):
        nmax = int(np.ceil(lengths[index] / step)) + 1
        t = np.arange(nmax) * step
        t = t[t < (lengths[index] - 0.75 * voxel)]
        if len(t):
            free[lin(origin + dirs[index] * t[:, None])] = True
        occ[lin(ends[index : index + 1])] = True
    state = np.zeros(n, np.uint8)
    state[free] = CoverageGrid.FREE
    state[occ] = CoverageGrid.OCCUPIED
    return state.reshape(shape)


def test_mixed_length_rays_match_per_ray_samples():
    rng = np.random.default_rng(0)
    origin = np.array([0.0, 1.5, 0.0])
    short = origin + rng.normal(size=(8, 3)) * 0.4
    long = origin + np.array([8.0, 0.2, -6.0]) + rng.normal(size=(4, 3)) * 0.2
    ends = np.vstack([short, long])
    lo = np.array([-2.0, -0.5, -10.0])
    hi = np.array([12.0, 4.0, 2.0])
    voxel = 0.05
    grid = build_grid([(origin, ends, None)], lo, hi, voxel, max_samples=64)
    assert np.array_equal(grid.state, _reference(origin, ends, lo, hi, voxel))
    assert int(grid.views.max()) == 1


def test_empty_camera_leaves_the_grid_unknown():
    lo = np.zeros(3)
    hi = np.ones(3)
    grid = build_grid([(np.zeros(3), np.zeros((0, 3)), None)], lo, hi, 0.25)
    assert int(grid.state.max()) == CoverageGrid.UNKNOWN
