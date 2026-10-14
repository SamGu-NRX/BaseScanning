"""Compare the batched march with individual slab queries and count repeated work."""

from dataclasses import replace

import numpy as np
import pytest
from test_coverage_views import views
from test_worker import _slab_volume

from recon import coverage
from recon.geometry import WallFrame

WALL = WallFrame(
    np.array([0.0, 1.0, 0.0]), np.array([1.0, 0, 0]), np.array([0, 0, 1.0]), 0.0, (0, 0.3)
)


def scalar_march(wall, volume, cells):
    step = volume.voxel
    raw = volume.vol if isinstance(volume, coverage.SeenFromTwo) else volume
    arrays = [
        np.full(len(cells), np.nan),
        np.zeros(len(cells)),
        np.full(len(cells), np.nan),
        np.zeros(len(cells)),
    ]
    fg, fc, og, oc = arrays
    heights = np.arange(coverage.FACING_BAND_M[0], coverage.FACING_BAND_M[1] + 1e-9, step)
    for c, cell in enumerate(cells):
        across = cell + coverage.CELL_M * np.array([0.25, 0.5, 0.75])
        front = coverage.wall_front(wall, raw, across)
        for out in np.arange(front + step, front + coverage.FACING_MAX_M, step):
            share, seen = coverage._slab(wall, volume, across, heights, [out])
            if share > 0:
                fg[c] = max(0, out - step - front)
                break
            if not seen:
                break
            fc[c] = out - front
        outs = front + np.arange(
            coverage.OVERHEAD_OUT_M[0], coverage.OVERHEAD_OUT_M[1] + 1e-9, step
        )
        for height in np.arange(0.3, coverage.OVERHEAD_MAX_M, step):
            share, seen = coverage._slab(wall, volume, across, [height], outs)
            if share > 0:
                og[c] = max(0, height - step)
                break
            if not seen:
                break
            oc[c] = height
    return arrays


@pytest.mark.parametrize("use_views", [False, True])
@pytest.mark.parametrize("kind", ["clear", "proud", "thin", "unknown"])
def test_batched_march_matches_scalar_stop_order(kind, use_views):
    occupied = [(-0.3, 0.3), (1.3, 1.7)] if kind == "proud" else [(-0.3, 0.0)]
    raw = _slab_volume(occupied, 1.1 if kind == "unknown" else 7.0)
    if kind == "thin":
        raw.tsdf[:, 20, 30] = -1.0
        raw.tsdf[:, 30, 16] = -1.0
    if use_views:
        frames, depths = views(7)
        volume = coverage.SeenFromTwo(raw, frames, depths)
    else:
        volume = raw
    cells = np.array([0.0, coverage.CELL_M])
    actual = coverage.free_space(WALL, volume, cells)
    expected = scalar_march(WALL, volume, cells)
    for a, b in zip(actual, expected, strict=True):
        np.testing.assert_array_equal(a, b)


def test_wall_front_gets_raw_volume_and_uses_no_visibility(monkeypatch):
    raw = _slab_volume([(-0.3, 0.0)], 7.0)
    frames, depths = views(2)
    view = coverage.SeenFromTwo(raw, frames, depths)
    calls = []
    real_front = coverage.wall_front

    def wall_front(wall, volume, across):
        assert volume is raw
        calls.append(len(across))
        return real_front(wall, volume, across)

    monkeypatch.setattr(coverage, "wall_front", wall_front)
    coverage.free_space(WALL, view, np.array([0.0, coverage.CELL_M]))
    assert calls == [3, 3]


def test_clear_march_batches_queries_and_builds_pairs_once(monkeypatch):
    raw = _slab_volume([(-0.3, 0.0)], 7.0)
    frames, depths = views(9)
    real_far = coverage.far_pairs
    pair_calls, query_sizes = [], []

    def far(centres):
        pair_calls.append(len(centres))
        return real_far(centres)

    monkeypatch.setattr(coverage, "far_pairs", far)
    view = coverage.SeenFromTwo(raw, frames, depths)

    def seen(points):
        query_sizes.append(len(points))
        return np.ones((len(points), len(frames)), bool)

    monkeypatch.setattr(view, "seen_through", seen)
    monkeypatch.setattr(coverage, "two_positions", lambda *_: pytest.fail("dense pair multiply"))
    cells = np.array([0.0, coverage.CELL_M])
    coverage.free_space(WALL, view, cells)
    facing_steps = len(np.arange(0.10, 0.05 + coverage.FACING_MAX_M, raw.voxel))
    overhead_steps = len(np.arange(0.3, coverage.OVERHEAD_MAX_M, raw.voxel))
    expected_calls = len(cells) * (
        int(np.ceil(facing_steps / coverage.SLABS_PER_BATCH))
        + int(np.ceil(overhead_steps / coverage.SLABS_PER_BATCH))
    )
    assert pair_calls == [len(frames)]
    assert len(query_sizes) == expected_calls == 24
    assert len(query_sizes) < (facing_steps + overhead_steps) * len(cells) // 10
    assert max(query_sizes) <= coverage.VIEW_POINTS_PER_BATCH


def test_compute_retains_ground_and_complete_cells_after_combining_prs(monkeypatch):
    raw = _slab_volume([], 7.0)
    frames, depths = views(2)
    wall = replace(WALL, s_range=(0.9 * coverage.CELL_M, 2.1 * coverage.CELL_M))
    ground = object()

    def wall_and_ground(wall, frames, depths, volume, mesh, cells, fitted_ground):
        assert fitted_ground is ground
        np.testing.assert_allclose(cells, [coverage.CELL_M])
        return np.zeros(len(cells), bool), np.zeros(len(cells)), np.zeros(len(cells))

    monkeypatch.setattr(coverage, "wall_and_ground", wall_and_ground)
    result = coverage.compute(wall, frames, depths, raw, None, ground)
    np.testing.assert_allclose(result.cells, [coverage.CELL_M])


def test_slab_batch_refuses_an_unsupported_march_axis():
    with pytest.raises(ValueError, match="slab axis must be height"):
        coverage._slabs(WALL, None, [0], [0], [0], axis=0)
