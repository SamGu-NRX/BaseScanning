"""Exact visibility and camera-pair checks across batching limits."""

from pathlib import Path

import numpy as np
import pytest

from recon import coverage
from recon.capture import Frame
from recon.depth import Depth
from recon.geometry import WallFrame


class ClearVolume:
    voxel = 0.05

    def observed(self, points):
        return np.ones(len(points), bool)

    def occupied(self, points):
        return np.zeros(len(points), bool)


def views(count):
    frames, depths = [], {}
    for j in range(count):
        pose = np.eye(4)
        pose[:3, 3] = [j * 0.13 - 0.3, 1.0, 4.0]
        angle = (j % 3 - 1) * 0.08
        c, s = np.cos(angle), np.sin(angle)
        pose[:3, :3] = [[c, 0, s], [0, 1, 0], [-s, 0, c]]
        frame = Frame(str(j), Path("unused"), 64, 48, np.array([24, 24, 32, 24]), pose)
        frames.append(frame)
        shape = (24, 32) if j % 2 else (48, 64)
        values = np.full(shape, 4.0, np.float32)
        values[: shape[0] // 3, : shape[1] // 3] = np.nan
        depths[frame.id] = Depth(values, frame.intrinsics, np.zeros((*shape, 3)), "lidar")
    return frames, depths


def scalar_visible(points, frames, depths):
    result = np.zeros((len(points), len(frames)), bool)
    for i, point in enumerate(points):
        for j, frame in enumerate(frames):
            delta = point - frame.center
            distance = np.linalg.norm(delta)
            if not 0.1 < distance <= coverage.MAX_RANGE_M:
                continue
            p = delta @ frame.cam_to_world[:3, :3]
            z = -p[2]
            if z <= 0.05:
                continue
            fx, fy, cx, cy = frame.intrinsics
            u, v = cx + fx * p[0] / z, cy - fy * p[1] / z
            margin = coverage.IMAGE_MARGIN
            if not frame.width * margin <= u <= frame.width * (1 - margin):
                continue
            if not frame.height * margin <= v <= frame.height * (1 - margin):
                continue
            values = depths[frame.id].depth
            h, w = values.shape
            scale = w / frame.width
            value = values[min(int(v * scale), h - 1), min(int(u * scale), w - 1)]
            result[i, j] = np.isfinite(value) and value >= z - coverage.tolerance(distance)
    return result


@pytest.mark.parametrize("frames", [0, 1, 2, 7, 9, 33, 65])
def test_packed_pair_test_matches_dense_relation(frames):
    rng = np.random.default_rng(40 + frames)
    centres = rng.normal(0, 0.2, (frames, 3))
    saw = rng.random((113, frames)) > 0.45
    far = coverage.far_pairs(centres)
    expected = coverage.two_positions(saw, centres, far)
    actual = coverage._two_positions_packed(saw, np.packbits(far.astype(bool), axis=1))
    np.testing.assert_array_equal(actual, expected)


def test_pair_test_does_not_require_first_view_to_have_a_far_partner():
    centres = np.array([[0, 0, 0], [-0.2, 0, 0], [0.2, 0, 0]])
    saw = np.array([[True, True, True], [True, True, False]])
    partners = np.packbits(coverage.far_pairs(centres).astype(bool), axis=1)
    np.testing.assert_array_equal(coverage._two_positions_packed(saw, partners), [True, False])


def test_batched_projection_matches_independent_scalar_visibility():
    frames, depths = views(9)
    view = coverage.SeenFromTwo(ClearVolume(), frames, depths)
    points = np.random.default_rng(5).uniform([-2, -1, -1], [3, 3, 5], (137, 3))
    expected = scalar_visible(points, frames, depths)
    np.testing.assert_array_equal(view.seen_through(points), expected)
    np.testing.assert_array_equal(view.observed(points), coverage.two_positions(expected, view.C))
    assert all(a is depths[f.id].depth for a, f in zip(view.depths, frames, strict=True))


def test_projection_queries_and_matrix_memory_are_bounded(monkeypatch):
    frames, depths = views(129)
    view = coverage.SeenFromTwo(ClearVolume(), frames, depths)
    monkeypatch.setattr(coverage, "VIEW_POINTS_PER_BATCH", 64)
    monkeypatch.setattr(coverage, "VIEW_SAMPLES_PER_BATCH", 1032)
    sizes = []

    def seen(points):
        sizes.append(len(points))
        return np.ones((len(points), len(frames)), bool)

    monkeypatch.setattr(view, "seen_through", seen)
    points = np.zeros((257, 3))
    assert view.observed(points).all()
    assert len(sizes) == 33 and max(sizes) == 8
    assert max(sizes) * len(frames) <= coverage.VIEW_SAMPLES_PER_BATCH


def test_one_view_or_one_position_never_claims_free_space():
    for count in (0, 1, 3):
        frames, depths = views(count)
        for frame in frames:
            frame.cam_to_world[:3, 3] = [0, 1, 4]
        view = coverage.SeenFromTwo(ClearVolume(), frames, depths)
        assert not view.observed(np.array([[0, 1, 1]])).any()


class FirstSlabObstacle(ClearVolume):
    def __init__(self, band):
        self.band = band

    def occupied(self, points):
        if self.band == "facing":
            # A thin hit in the first outward slab, too small to become the wall front.
            return np.isclose(points[:, 2], 0.1) & np.isclose(points[:, 1], 1.0)
        return np.isclose(points[:, 1], 0.3) & np.isclose(points[:, 2], 0.3)


def free_space_from_positions(raw, xs):
    frames, depths = views(len(xs))
    for frame, x in zip(frames, xs, strict=True):
        frame.cam_to_world[:3, :3] = np.eye(3)
        frame.cam_to_world[:3, 3] = [x, 1.0, 4.0]
        depths[frame.id].depth[:] = 4.0
    wall = WallFrame(
        np.array([0.0, 1.0, 0.0]), np.array([1.0, 0, 0]), np.array([0, 0, 1.0]), 0.0, (0, 0.3)
    )
    cells = np.array([0.0])
    arrays = coverage.free_space(wall, coverage.SeenFromTwo(raw, frames, depths), cells)
    return coverage.CellCoverage(cells, np.zeros(1, bool), np.zeros(1), *arrays)


@pytest.mark.parametrize("band", ["facing", "overhead"])
@pytest.mark.parametrize(
    ("xs", "supported"),
    [([0.075], False), ([0.025, 0.125], False), ([-0.125, 0.275], True)],
    ids=["one_camera", "nearby_cameras", "separated_cameras"],
)
def test_first_occupied_slab_needs_two_views_before_exporting_measurement(band, xs, supported):
    cov = free_space_from_positions(FirstSlabObstacle(band), xs)
    gap = cov.facing_gap if band == "facing" else cov.overhead_clearance
    clear = cov.facing_clear if band == "facing" else cov.overhead_clear
    entries = [entry for entry in coverage.observed(cov) if entry["band"] == band]
    measurements = coverage.measurements(cov, "wall", 0.5)[0 if band == "facing" else 1]
    # An occupied first slab never counts as empty, even when its measurement is unsupported.
    assert clear[0] == 0.0
    if supported:
        assert gap[0] == pytest.approx(0.0 if band == "facing" else 0.25)
        assert entries == [{"band": band, "span_ft": [0.0, 0.5]}]
        assert len(measurements) == 1
    else:
        assert np.isnan(gap[0])
        assert entries == []
        assert measurements == []


@pytest.mark.parametrize(
    ("xs", "supported"),
    [([0.075], False), ([0.025, 0.125], False), ([-0.125, 0.275], True)],
    ids=["one_camera", "nearby_cameras", "separated_cameras"],
)
def test_empty_volume_needs_two_views_before_exporting_clear_space(xs, supported):
    cov = free_space_from_positions(ClearVolume(), xs)
    assert np.isnan(cov.facing_gap[0]) and np.isnan(cov.overhead_clearance[0])
    assert coverage.measurements(cov, "wall", 0.5) == ([], [])
    entries = coverage.observed(cov)
    if supported:
        assert cov.facing_clear[0] > 0 and cov.overhead_clear[0] > 0
        assert {entry["band"] for entry in entries} == {"facing", "overhead"}
        assert all(entry["out_ft"] > 0 for entry in entries)
    else:
        assert cov.facing_clear[0] == cov.overhead_clear[0] == 0.0
        assert entries == []
