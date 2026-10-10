"""Observation-contract tests: what observations may and may not imply about the world.

These encode the study's epistemic rules as executable tests:
- a 2D polygon attests extent, never a measured ground height;
- an empty denominator is unknown, not complete observation;
- incompatible worlds can permit identical observations (unidentifiable states stay unknown);
- sparse laser returns do not prove the gaps between them empty.
"""

from __future__ import annotations

import numpy as np

from capture_coverage.reference import Reference, coverage_fraction
from capture_coverage.scenes import Ellipsoid, GroundPatch
from capture_coverage.sim import DEPTH_SIGMA, render_depth
from tests.test_reference import flat_walk, straight_wall_scene


def test_2d_polygon_ground_has_no_measured_height():
    patch = GroundPatch(-1.0, 12.0, -0.2, 4.0, "concrete", measured=False)
    h = patch.height_at(np.array([0.0, 5.0]), np.array([1.0, 1.0]))
    assert np.isnan(h).all(), "a 2D polygon supplies no measured ground height"
    scene = straight_wall_scene()
    scene.grounds = [patch]
    h2 = scene.true_ground_height(np.array([0.0]), np.array([1.0]))
    assert np.isnan(h2).all(), "the scene cannot conjure a height the polygon never measured"
    ref = Reference(scene, flat_walk(look_y=0.0))
    band = ref.ground_band(np.array([0.0, 1.0]))
    assert not band["exists"].any(), "unmeasured ground: nothing to claim, status unknown"


def test_empty_denominator_is_unknown_not_complete():
    assert np.isnan(coverage_fraction(0, 0)), "no samples: unknown, not 100% observed"
    assert np.isnan(coverage_fraction(5, 0)), "an impossible tally must not read as coverage"
    assert coverage_fraction(3, 4) == 0.75
    assert coverage_fraction(4, 4) == 1.0


def test_incompatible_worlds_permit_identical_observations():
    """A bush hides a wall hole in world A; world B has a solid wall behind the same bush. The
    camera sees byte-identical depth in both - the difference is unidentifiable from this
    capture, so neither world may be claimed resolved on it."""
    from capture_coverage.scenes import Box

    hedge = Box(np.array([9.0, 0.0, 0.0]), np.array([11.0, 1.6, 0.6]), "bush")  # tall: covers the opening from oblique cameras too
    hole = straight_wall_scene(openings=[(9.7, 10.3, 0.0, 0.9)], occluders=[hedge])
    solid = straight_wall_scene(occluders=[hedge])
    walk = flat_walk(xs=np.array([8.0, 10.0, 12.0]), look_y=1.0)
    # per-render seeded rng: corresponding frames get identical noise, so any depth difference
    # between the worlds is geometry, not sampling order
    depths = [
        render_depth(scene, walk.positions[i], walk.rotations[i], np.random.default_rng(100 + i), sigma=DEPTH_SIGMA)
        for scene in (hole, solid)
        for i in range(3)
    ]
    hole_d, solid_d = depths[:3], depths[3:]
    for a, b in zip(hole_d, solid_d, strict=True):
        same = (np.isnan(a) & np.isnan(b)) | (a == b)
        assert same.all(), "the hidden difference must not leak into the depth image"
    # and the oracle marks the region behind the bush unseeable in both worlds
    for scene in (hole, solid):
        ref = Reference(scene, walk)
        pts = np.array([[10.0, 0.5, 0.0], [10.0, 0.8, 0.0]])  # wall face behind the bush
        counts, _ = ref.visible(pts)
        assert (counts == 0).all(), "no camera reaches behind the bush"


def test_sparse_returns_do_not_prove_gaps_empty():
    """A depth frame that returns nothing over a region leaves that region unknown: the oracle
    marks it unoccupied (nothing solid there in this world) but unseen - not clear."""
    scene = straight_wall_scene()
    walk = flat_walk(xs=np.array([10.0]), look_y=1.0)
    ref = Reference(scene, walk)
    # samples no camera in this walk reaches: above the frame and behind it
    pts = np.array([[10.0, 2.6, 1.2], [10.0, 1.0, 3.2]])
    occ = ref.occupied(pts)
    assert not occ.any(), "nothing solid between the returns in this world"
    counts, _ = ref.visible(pts)
    assert (counts == 0).all(), "the chosen samples are outside every camera's view"
    # unoccupied + unseen = UNKNOWN; only unoccupied + seen can be called clear
    print("unknown samples:", (~occ & (counts == 0)).sum())
