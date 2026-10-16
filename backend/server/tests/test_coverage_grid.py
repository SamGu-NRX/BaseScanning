"""build_grid keeps the half-voxel sample rule when rays are clipped to the box."""

from __future__ import annotations

import numpy as np

from server.eval.coverage import build_grid
from server.eval.sceneio import CoverageGrid


def _reference(origin, ends, lo, hi, voxel, surf=None):
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
        if surf is None or surf[index]:
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


def test_rays_past_the_grid_match_per_ray_samples():
    origin = np.array([1.0, 1.2, 1.0])
    ends = np.array(
        [
            [80.0, 40.0, -30.0],
            [2.0, 1.4, 1.5],
            [-40.0, 10.0, 90.0],
            [1.2, 80.0, 1.0],
            [30.0, 1.1, 1.2],
        ]
    )
    surf = np.array([True, False, True, True, False])
    lo = np.array([0.0, 0.0, 0.0])
    hi = np.array([6.0, 3.0, 4.0])
    voxel = 0.1
    grid = build_grid([(origin, ends, surf)], lo, hi, voxel, max_samples=32)
    assert np.array_equal(grid.state, _reference(origin, ends, lo, hi, voxel, surf))
    assert int(grid.views.max()) == 1


def test_two_cameras_count_a_shared_voxel_once_each():
    lo = np.zeros(3)
    hi = np.array([4.0, 2.0, 4.0])
    voxel = 0.25
    ends = np.array([[2.0, 1.0, 2.0]])
    grid = build_grid(
        [
            (np.array([0.2, 1.0, 0.2]), ends, None),
            (np.array([0.2, 1.0, 3.5]), ends, None),
        ],
        lo,
        hi,
        voxel,
    )
    assert int(grid.views.max()) == 2
    first = _reference(np.array([0.2, 1.0, 0.2]), ends, lo, hi, voxel)
    second = _reference(np.array([0.2, 1.0, 3.5]), ends, lo, hi, voxel)
    merged = np.maximum(first, second)
    assert np.array_equal(grid.state, merged)


def test_generator_ray_sets_match_a_list():
    origin = np.array([0.2, 1.0, 0.2])
    ends = np.array([[1.5, 1.0, 1.5], [3.0, 0.4, 0.5]])
    lo = np.zeros(3)
    hi = np.ones(3) * 4
    listed = build_grid([(origin, ends, None)], lo, hi, 0.2)
    generated = build_grid(((origin, ends, None) for _ in range(1)), lo, hi, 0.2)
    assert np.array_equal(listed.state, generated.state)
    assert np.array_equal(listed.views, generated.views)


def test_empty_camera_leaves_the_grid_unknown():
    lo = np.zeros(3)
    hi = np.ones(3)
    grid = build_grid([(np.zeros(3), np.zeros((0, 3)), None)], lo, hi, 0.25)
    assert int(grid.state.max()) == CoverageGrid.UNKNOWN
    assert int(grid.views.max()) == 0
