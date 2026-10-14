"""Vertical samples follow the fitted ground across and away from the wall."""

import numpy as np
import pytest

from recon import coverage
from recon.geometry import Ground, WallFrame


@pytest.mark.parametrize("axis", [1, 2])
@pytest.mark.parametrize("slope", [0.25, -0.25])
def test_vertical_slabs_are_relative_to_local_ground(axis, slope):
    wall = WallFrame(
        np.array([0.0, 1.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
        0.0,
        (0.0, 3.0),
    )
    ground = Ground(np.zeros(3), np.array([-slope, 1.0, -0.1]), 0.0, 100)
    queried = []

    class Volume:
        def occupied(self, points):
            queried.append(points.copy())
            return np.zeros(len(points), bool)

        def observed(self, points):
            return np.ones(len(points), bool)

    coverage._slabs(wall, Volume(), [2.0, 3.0], [1.0, 2.0], [0.1, 0.5], axis=axis, ground=ground)
    s, h, out = np.meshgrid([2.0, 3.0], [1.0, 2.0], [0.1, 0.5], indexing="ij")
    points = queried[0]
    np.testing.assert_allclose(points[:, 1], h.ravel() + ground.height_at(s.ravel(), out.ravel()))


def test_uphill_eave_reports_clearance_above_cell_ground():
    wall = WallFrame(
        np.array([0.0, 1.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
        0.0,
        (0.0, 3.0),
    )
    ground = Ground(np.zeros(3), np.array([-0.5, 1.0, 0.0]), 0.0, 100)

    class Eave:
        voxel = 0.05

        def occupied(self, points):
            # Horizontal eave at world y=2.5 m; the cell ground is about 1 m higher.
            return points[:, 1] >= 2.5

        def observed(self, points):
            return np.ones(len(points), bool)

    _, _, gap, clear = coverage.free_space(wall, Eave(), np.array([2.0]), ground=ground)
    assert gap[0] == pytest.approx(1.40)
    assert clear[0] == pytest.approx(1.40)


def test_wall_band_heights_are_relative_to_local_ground(monkeypatch):
    wall = WallFrame(
        np.array([0.0, 1.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
        0.0,
        (0.0, 3.0),
    )
    ground = Ground(np.zeros(3), np.array([-0.5, 1.0, -0.1]), 0.0, 100)
    queried = []

    def seen(points, *args):
        queried.append(points.copy())
        return np.ones((len(points), 2), bool)

    monkeypatch.setattr(coverage, "seen_by", seen)
    monkeypatch.setattr(
        coverage, "two_positions", lambda visibility, centres: visibility.all(axis=1)
    )
    monkeypatch.setattr(coverage, "face_offsets", lambda *args: np.zeros(1))
    coverage.wall_and_ground(wall, [], {}, None, None, np.array([2.0]), ground)
    points = queried[0]
    height = np.tile(np.linspace(0.05, coverage.WALL_TOP_M, coverage.WALL_ROWS), 2)
    np.testing.assert_allclose(points[:, 1], height + ground.height_at(points[:, 0], points[:, 2]))
