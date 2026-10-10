"""Reference truth tests: the oracle must be right about walls, occlusion, and visibility."""

from __future__ import annotations

import numpy as np

from capture_coverage.reference import CELL, Reference
from capture_coverage.scenes import Box, Ellipsoid, GroundPatch, Scene
from capture_coverage.sim import Walk


def flat_walk(xs=None, standoff=2.4, height=1.5, look_y=None):
    xs = np.arange(-1.0, 21.0, 0.55) if xs is None else np.asarray(xs, float)
    pos = np.stack([xs, np.full(len(xs), height), np.full(len(xs), standoff)], axis=1)
    if look_y is None:
        rot = np.tile(np.eye(3), (len(xs), 1, 1))  # cam looks along -z: straight at the wall
    else:
        target = np.stack([xs, np.full(len(xs), look_y), np.zeros(len(xs))], axis=1)
        f = target - pos
        f /= np.linalg.norm(f, axis=-1)[:, None]
        zc = -f
        xc = np.cross([0.0, 1.0, 0.0], zc)
        xc /= np.linalg.norm(xc, axis=-1)[:, None]
        yc = np.cross(zc, xc)
        rot = np.stack([xc, yc, zc], axis=1)  # rows = camera axes in world coords
    return Walk(pos, rot, pos.copy(), rot.copy())  # reported = true (perfect odometry)


def straight_wall_scene(**kw):
    s = Scene("t", wall_x0=-2.0, wall_x1=22.0, **kw)
    s.grounds = []
    return s


def test_wall_band_exists_only_where_wall_is():
    scene = straight_wall_scene(openings=[(5.0, 7.0, 0.0, 2.1)])
    ref = Reference(scene, flat_walk())
    cells = np.arange(0.0, 10.0, CELL)
    truth = ref.wall_band(cells)
    exists = truth["exists"].mean(axis=(1, 2)) > 0
    assert not exists[np.argmin(np.abs(cells - 6.0))], "the doorway is a hole"
    assert exists[np.argmin(np.abs(cells - 4.0))], "solid wall at 4 m"
    assert exists[np.argmin(np.abs(cells - 8.0))], "solid wall at 8 m"


def test_wall_band_seen_everywhere_from_standoff():
    scene = straight_wall_scene()
    ref = Reference(scene, flat_walk())
    cells = np.arange(0.0, 10.0, CELL)
    truth = ref.wall_band(cells)
    seen = truth["seen"].mean(axis=(1, 2))
    assert (seen > 0.9).all(), "a plain wall seen by 40 cameras at 2.4 m: every cell well-lit"


def test_occluder_hides_low_band_behind_it():
    """A 1.2 m bush flush to the wall hides the wall band below 1.2 m at its s."""
    occ = Ellipsoid(np.array([14.5, 0.6, 0.25]), np.array([0.5, 0.6, 0.25]))
    scene = straight_wall_scene(occluders=[occ])
    ref = Reference(scene, flat_walk())
    cells = np.arange(13.0, 16.0, CELL)
    truth = ref.wall_band(cells)
    seen = truth["seen"].mean(axis=(1, 2))
    behind = np.abs(cells - 14.5) < 0.3
    assert (seen[behind] < 0.75).all(), "low band behind a 1.2 m bush is mostly hidden"
    clear_col = np.abs(cells - 13.0) < CELL
    assert (seen[clear_col] > 0.9).all(), "the wall beside the bush stays visible"


def test_ground_band_seen_on_flat_concrete():
    """A pitched-down camera sees a middle band of each ground strip (near and far samples fall
    outside a phone's vertical frame - that is a capture limit, not an oracle failure)."""
    scene = straight_wall_scene()
    scene.grounds = [
        GroundPatch(-4, 26, -6.0, 12.0, "lawn"),
        GroundPatch(-1.0, 12.0, -0.2, 4.0, "concrete"),
    ]
    ref = Reference(scene, flat_walk(look_y=0.0))
    cells = np.arange(0.0, 10.0, CELL)
    truth = ref.ground_band(cells)
    seen = truth["seen"]  # (cells, cols, outs)
    assert (seen.max(axis=(1, 2)) > 0.9).all(), "each cell's strip is seen somewhere by a pitched walk"


def test_two_position_needs_two_far_apart_positions():
    pos = np.array([[3.0, 1.5, 2.4]])
    rot = np.tile(np.eye(3), (1, 1, 1))
    ref = Reference(straight_wall_scene(), Walk(pos, rot, pos.copy(), rot.copy()))
    target = np.array([[3.0, 1.0, 0.0]])  # straight ahead: inside the ~36 deg half-FOV
    counts, two = ref.visible(target)
    assert counts[0] == 1
    assert not two[0], "a single position cannot be two positions"


def test_two_position_met_by_two_far_cameras():
    xs = np.array([3.0, 3.0, 5.0, 5.0])  # both positions see (4,1,0); pairs across are 2 m apart
    pos = np.stack([xs, np.full(4, 1.5), np.full(4, 2.4)], axis=1)
    rot = np.tile(np.eye(3), (4, 1, 1))
    ref = Reference(straight_wall_scene(), Walk(pos, rot, pos.copy(), rot.copy()))
    target = np.array([[4.0, 1.0, 0.0]])
    counts, two = ref.visible(target)
    assert counts[0] >= 2 and two[0], "two cameras 2 m apart both see the target"


def test_occupied_marks_solid_interiors():
    box = Box(np.array([14.0, 0.0, 0.2]), np.array([16.0, 0.75, 1.2]), "bin")
    scene = straight_wall_scene(occluders=[box])
    ref = Reference(scene, flat_walk())
    pts = np.array(
        [
            [15.0, 0.4, 0.7],  # inside the bin
            [15.0, 1.5, 0.7],  # in the air above it
            [15.0, 0.4, 0.0],  # inside the wall behind it
        ]
    )
    occ = ref.occupied(pts)
    assert occ[0], "inside the bin"
    assert not occ[1], "air is free"
    assert occ[2], "inside the wall"


def test_wall_face_and_behind():
    """The outward side faces the cameras (z > 0): a ray from z=3 hits the face at z=0, and a
    ray aimed mid-wall is blocked before it."""
    ref = Reference(straight_wall_scene(), flat_walk())
    origin = np.array([[10.0, 1.0, 3.0]])
    face = np.array([[10.0, 1.0, 0.0]])
    d = face - origin
    hit = ref._moller(origin, d / np.linalg.norm(d, axis=-1)[:, None], None)
    assert np.isfinite(hit[0]) and abs(hit[0] - 3.0) < 1e-3, "the wall face at z=0"
    beyond = np.array([[10.0, 1.0, -0.075]])  # mid-wall
    d2 = beyond - origin
    hit2 = ref._moller(origin, d2 / np.linalg.norm(d2, axis=-1)[:, None], None)
    assert np.isfinite(hit2[0]) and hit2[0] < 3.075 - 1e-3, "blocked before mid-wall"
