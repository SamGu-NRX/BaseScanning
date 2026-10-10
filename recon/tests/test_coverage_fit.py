"""The fitted extent bounds coverage, and ground samples sit on the fitted ground.

Synthetic cases, no captures: cells stop where the fitted wall stops, so no exported
observation exceeds evidence sampled inside the fit, and on sloped ground the ground samples
follow the plane the geometry fit instead of the horizontal one through the meter's foot.
"""

from pathlib import Path

import numpy as np
import pytest

from recon import coverage, scene
from recon.capture import FEET, Capture, Frame
from recon.coverage import CELL_M, GROUND_MAX_M, cell_grid, compute, observed
from recon.depth import Depth
from recon.fusion import Mesh, Volume, integrate, mesh
from recon.geometry import Ground, WallFrame
from recon.pipeline import PLUS_MINUS_FT, WALL_SOURCE

LO, HI = 0.10, 2.0  # a fitted stretch ending off the half-foot grid on both ends
LO_FT, HI_FT = round(LO / FEET, 3), round(HI / FEET, 3)  # 0.328 and 6.562

EMPTY = Mesh(
    np.zeros((0, 3), np.float32),
    np.zeros((0, 3), np.uint32),
    np.zeros((0, 3), np.float32),
    np.zeros((0, 3), np.uint8),
)


def _wall(lo: float = LO, hi: float = HI) -> WallFrame:
    """A wall along +x through the origin facing +z, fitted from lo to hi."""
    return WallFrame(
        np.array([0.0, 1.0, 0.0]), np.array([1.0, 0, 0]), np.array([0, 0, 1.0]), 0.0, (lo, hi)
    )


def _frames_at(xs: list[float], z: float = 2.0) -> list[Frame]:
    """Cameras 1 m up looking along -z at the wall plane z = 0."""
    out = []
    for i, x in enumerate(xs):
        T = np.eye(4)
        T[:3, 3] = [x, 1.0, z]
        out.append(Frame(f"k{i}", Path(f"k{i}.jpg"), 640, 480, np.array([200.0, 200, 320, 240]), T))
    return out


def _unseen_volume() -> Volume:
    """A volume nothing was fused into: free_space finds no front and nothing facing the wall."""
    shape = (21, 65, 151)
    return Volume(
        np.array([-0.5, 0.0, -0.5]),
        0.05,
        shape,
        np.ones(shape, np.float32),
        np.zeros(shape, np.float32),
        np.zeros((*shape, 3), np.float32),
        np.zeros(shape, np.float32),
    )


def _always_seen(points, *_):
    return np.ones((len(points), 2), bool)


def test_cell_grid_clips_unaligned_ends_and_keeps_aligned_ones():
    starts, ends = cell_grid(LO, HI)
    assert starts[0] == pytest.approx(LO) and ends[-1] == pytest.approx(HI)
    assert np.allclose(np.diff(starts[1:]), CELL_M)  # interior edges stay on the half-foot grid
    assert (starts < ends).all()
    for lo, hi in [(0.0, 1.9812), (-0.3048, 0.3048), (0.1524, 1.2192)]:
        starts, ends = cell_grid(lo, hi)
        np.testing.assert_allclose(starts, np.arange(np.floor(lo / CELL_M) * CELL_M, hi, CELL_M))
        np.testing.assert_allclose(ends, starts + CELL_M)  # aligned ends keep full-width cells
    starts, ends = cell_grid(0.3, 0.3)  # a fit of no length establishes nothing
    assert len(starts) == 0 and len(ends) == 0


def test_no_observed_interval_reaches_past_the_fitted_extent(monkeypatch):
    # Every sample seen: the wall and ground bands still stop at the fit, 0.10 to 2.0 m, not at
    # the old floor-to-grid cells that began at 0 and ran half a foot past the fit's end.
    monkeypatch.setattr(coverage, "seen_by", _always_seen)
    cov = compute(_wall(), _frames_at([-0.2, 0.2]), {}, _unseen_volume(), EMPTY)
    assert cov.cells[0] == pytest.approx(LO) and cov.ends[-1] == pytest.approx(HI)
    entries = observed(cov)
    assert entries, "the setup observes nothing, so the bounds below hold vacuously"
    for e in entries:
        assert e["span_ft"][0] >= LO_FT - 1e-9, e
        assert e["span_ft"][1] <= HI_FT + 1e-9, e
    assert {"band": "wall", "span_ft": [LO_FT, HI_FT]} in entries
    assert {"band": "ground", "span_ft": [LO_FT, HI_FT], "out_ft": 10.0} in entries


def test_a_surface_beyond_the_fit_adds_no_observation():
    # The reconstructed wall runs half a metre past each end of the fit, and every camera sees
    # it: the samples outside the fit are real wall, but the fit established none of it, so the
    # observation stops at 0.10 and 2.0 m.
    frames = _frames_at([-0.5, -0.1, 0.3, 0.7, 1.1, 1.5, 1.9, 2.3])
    depths = {
        f.id: Depth(
            np.full((480, 640), 2.0, np.float32),
            f.intrinsics,
            np.zeros((480, 640, 3), np.uint8),
            "lidar",
        )
        for f in frames
    }
    poses = {f.id: f.cam_to_world for f in frames}
    vol = integrate(depths, poses, voxel=0.05)
    m = mesh(vol)
    cov = compute(_wall(), frames, depths, vol, m)
    assert cov.wall.all(), "the setup observes nothing, so the bounds below hold vacuously"
    assert [e["span_ft"] for e in observed(cov) if e["band"] == "wall"] == [[LO_FT, HI_FT]]


def _cov_for_scene() -> coverage.CellCoverage:
    """A coverage whose single observed cell spans the whole fit, for scene.build."""
    starts, ends = cell_grid(LO, HI)
    return coverage.CellCoverage(
        starts,
        np.ones(len(starts), bool),
        np.full(len(starts), GROUND_MAX_M),
        np.full(len(starts), np.nan),
        np.zeros(len(starts)),
        np.full(len(starts), np.nan),
        np.zeros(len(starts)),
        ends,
    )


def test_a_longer_retained_baseline_adds_no_observation():
    # scene.build keeps an older, longer baseline over the union with the fit; the observations
    # still stop at the fit, so the retained line cannot back wall the fit never established.
    prior = {
        "schema_version": "1.0",
        "meter": {"pos": [0.0, 5.0, 0.0], "wall_id": "w"},
        "walls": [{"id": "w", "baseline": [[-4.0, 0.0], [8.0, 0.0]]}],
        "keyframes": [],
    }
    c = Capture("scan-bundle", Path("b"), [], 0.0, np.array([0.0, 1.524, 0.0]), None, prior)
    doc = scene.build(c, _wall(), _cov_for_scene(), WALL_SOURCE["lidar"], PLUS_MINUS_FT["lidar"])
    assert doc["walls"][0]["baseline"] == [[-4.0, 0.0], [8.0, 0.0]]  # the union is still kept
    for e in doc["coverage"]["observed"]:
        assert e["span_ft"][0] >= LO_FT - 1e-9 and e["span_ft"][1] <= HI_FT + 1e-9, e


def test_grid_aligned_flat_ground_output_is_unchanged(monkeypatch):
    # A fit that begins and ends on the half-foot grid gets exactly the cells, spans and ground
    # depths the old first-to-last grid produced, on flat ground with every sample seen.
    monkeypatch.setattr(coverage, "seen_by", _always_seen)
    for lo, hi in [(0.0, 1.9812), (-0.3048, 0.3048), (0.1524, 1.2192)]:
        cov = compute(_wall(lo, hi), _frames_at([-0.2, 0.2]), {}, _unseen_volume(), EMPTY)
        old = np.arange(np.floor(lo / CELL_M) * CELL_M, hi - 1e-9, CELL_M)
        np.testing.assert_allclose(cov.cells, old)
        np.testing.assert_allclose(cov.ends, old + CELL_M)
        span_ft = [round(lo / FEET, 3), round(hi / FEET, 3)]
        expected = [{"band": "wall", "span_ft": span_ft}] + [
            {"band": "ground", "span_ft": span_ft, "out_ft": level}
            for level in (2.0, 4.0, 6.0, 8.0, 10.0)
        ]
        assert observed(cov) == expected


def _sloped_ground() -> Ground:
    """Ground rising 1 in 3 along +x, through the origin."""
    return Ground(np.zeros(3), np.array([-0.3, 1.0, 0.0]) / np.sqrt(1.09), 0.0, 100)


def test_ground_samples_follow_the_fitted_plane(monkeypatch):
    # The ground samples sit on the fitted plane (their height is 0.3 s above the meter's
    # ground), and the wall samples keep their own heights.
    calls = []

    def record(points, *_):
        calls.append(points.copy())
        return _always_seen(points)

    monkeypatch.setattr(coverage, "seen_by", record)
    compute(_wall(), _frames_at([-0.2, 0.2]), {}, _unseen_volume(), EMPTY, _sloped_ground())
    assert len(calls) == 2  # the wall band first, then the ground band
    wall_pts, ground_pts = calls
    assert wall_pts[:, 1].min() > 0.0 and wall_pts[:, 1].max() <= coverage.WALL_TOP_M + 1e-9
    np.testing.assert_allclose(ground_pts[:, 1], 0.3 * ground_pts[:, 0], atol=1e-9)


def test_sloped_ground_seen_only_on_the_plane(monkeypatch):
    # The same slope, seen by a depth test that only credits samples lying on the surface, and
    # only right of a pit at s = 0.5: with the fitted plane the visible ground reads observed and
    # the pit stays unobserved; on the old horizontal plane through the meter nothing does.
    def plane_seen(points, *_):
        on = (np.abs(points[:, 1] - 0.3 * points[:, 0]) <= 0.005) & (points[:, 0] > 0.5)
        return np.repeat(on[:, None], 2, 1)

    monkeypatch.setattr(coverage, "seen_by", plane_seen)
    seen = compute(_wall(), _frames_at([-0.2, 0.2]), {}, _unseen_volume(), EMPTY, _sloped_ground())
    missed = compute(_wall(), _frames_at([-0.2, 0.2]), {}, _unseen_volume(), EMPTY)
    assert (seen.ground_out[seen.cells >= 0.6] == GROUND_MAX_M).all()
    assert (seen.ground_out[seen.cells < 0.6] == 0.0).all()  # the pit: unseen stays unobserved
    assert (missed.ground_out == 0.0).all()  # the horizontal plane hangs off the slope entirely
    assert {"band": "ground", "span_ft": [2.0, HI_FT], "out_ft": 10.0} in observed(seen)


def test_an_empty_fit_yields_no_cells_and_no_observations(monkeypatch):
    monkeypatch.setattr(coverage, "seen_by", _always_seen)
    cov = compute(_wall(0.3, 0.3), _frames_at([-0.2, 0.2]), {}, _unseen_volume(), EMPTY)
    assert len(cov.cells) == 0
    assert observed(cov) == []


def test_measured_spans_stop_at_the_fitted_extent():
    # A facing gap measured over the last two cells reports its span up to the clipped end, 0.40,
    # not half a foot past it.
    starts, ends = cell_grid(LO, 0.4)
    cov = coverage.CellCoverage(
        starts,
        np.zeros(len(starts), bool),
        np.zeros(len(starts)),
        np.array([np.nan, 1.0, 1.0]),
        np.zeros(len(starts)),
        np.full(len(starts), np.nan),
        np.zeros(len(starts)),
        ends,
    )
    facing, overheads = coverage.measurements(cov, "wall", 0.5)
    assert overheads == []
    assert facing == [
        {
            "wall_id": "wall",
            "span_ft": [0.5, round(0.4 / FEET, 3)],
            "depth_ft": round(1.0 / FEET, 3),
            "plus_minus_ft": 0.5,
        }
    ]
