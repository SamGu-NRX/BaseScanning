"""World construction, per-scenario visibility patterns, and the pan control.

The visibility assertions are the rigs' contract: each blind spot is deliberate,
with margin, and a change in any pattern changes what the study's scenarios mean.
"""

from __future__ import annotations

import numpy as np

from nonlidar_observability.identifiability import evaluate
from nonlidar_observability.observe import observe, visible_landmarks
from nonlidar_observability.scenarios import (
    GRID,
    NOMINAL,
    pan_pair_for,
    reobserve_for,
    scenarios,
)
from nonlidar_observability.worlds import (
    ALONG_OFFSETS_FT,
    LANDMARK_IDS,
    WallWorld,
    baseline_dir,
    landmarks,
    outward_normal,
    wall_point,
)

END_RIGHT = ("end_right", "top_right")
TOPS = ("top_left", "top_right")

# A reduced grid for the control checks (the full grid runs in run.py).
SMALL_GRID = {
    "theta_deg": (-2.0, 0.0, 2.0),
    "d_ft": (19.0, 20.0, 21.0),
    "s0_ft": (-11.0, -10.0, -9.0),
    "s1_ft": (10.0, 12.0, 16.0),  # 12 and 16 both keep the right end out of frame
    "h_ft": (8.0, 9.0, 10.0),
}


def test_outward_normal_follows_the_schema_rule() -> None:
    # Schema: a baseline's outward side is its direction turned 90 degrees
    # clockwise in plan seen from above (+y).
    np.testing.assert_allclose(outward_normal(0.0), [0.0, 0.0, 1.0], atol=1e-12)
    np.testing.assert_allclose(outward_normal(90.0), [-1.0, 0.0, 0.0], atol=1e-12)
    for theta in (-4.0, 2.5, 45.0):
        a = baseline_dir(theta)
        n = outward_normal(theta)
        assert abs(float(a @ n)) < 1e-12  # perpendicular
        assert abs(np.linalg.norm(n) - 1.0) < 1e-12


def test_wall_point_geometry() -> None:
    p = wall_point(NOMINAL, 0.0, 0.0)
    # The foot of the perpendicular from the meter (origin) is the face point.
    np.testing.assert_allclose(p, [0.0, 0.0, -20.0], atol=1e-12)
    lm = landmarks(NOMINAL)
    assert set(lm) == set(LANDMARK_IDS)
    # Interior marks sit at fixed s offsets past the left end, at ground level.
    np.testing.assert_allclose(lm["along_1"], [-6.0, 0.0, -20.0], atol=1e-12)
    assert ALONG_OFFSETS_FT == (4.0, 9.0)


def test_grid_covers_the_nominal_world() -> None:
    assert NOMINAL.theta_deg in GRID["theta_deg"]
    assert NOMINAL.d_ft in GRID["d_ft"]
    assert NOMINAL.s0_ft in GRID["s0_ft"]
    assert NOMINAL.s1_ft in GRID["s1_ft"]
    assert NOMINAL.h_ft in GRID["h_ft"]


def test_world_facts_round_trip() -> None:
    w = WallWorld(theta_deg=3.0, d_ft=21.0, s0_ft=-9.0, s1_ft=11.0, h_ft=8.0)
    assert w.facts() == {
        "orientation_deg": 3.0,
        "distance_ft": 21.0,
        "left_end_ft": -9.0,
        "right_end_ft": 11.0,
        "height_ft": 8.0,
    }


def test_rig_visibility_patterns() -> None:
    expected = {
        "one-view": {k: ["kf0"] for k in LANDMARK_IDS},
        "two-view": {k: ["kf0", "kf1"] for k in LANDMARK_IDS},
        "two-view-pan-only": {
            **{k: ["kf0"] for k in LANDMARK_IDS},
            "end_right": ["kf0", "kf1"],
            "along_2": ["kf0", "kf1"],
            "top_right": ["kf0", "kf1"],
        },
        "two-view-top-blind": {
            **{k: ["kf0", "kf1"] for k in LANDMARK_IDS if k not in TOPS},
            "top_left": [],
            "top_right": [],
        },
        "two-view-end-blind": {
            **{k: ["kf0", "kf1"] for k in LANDMARK_IDS if k not in END_RIGHT},
            "end_right": [],
            "top_right": [],
        },
    }
    for s in scenarios():
        obs = observe(s.true_world, list(s.cameras))
        pattern = visible_landmarks(obs)
        assert pattern == expected[s.name], (
            f"{s.name}: visibility {pattern} != designed {expected[s.name]}"
        )


def test_reobserve_control_adds_nothing() -> None:
    """The zero-information control: duplicated frames change no verdict, no pixel."""
    from nonlidar_observability.observe import observe

    for s in scenarios():
        control = reobserve_for(s.cameras)
        base_centers = {tuple(np.round(c.center, 9)) for c in s.cameras}
        twin_centers = {tuple(np.round(c.center, 9)) for c in control.cameras}
        assert twin_centers == base_centers
        base_obs = observe(s.true_world, list(s.cameras))
        ext_obs = observe(s.true_world, list(s.cameras) + list(control.cameras))
        # Twins may carry their own ids, but every original landmark's pixels
        # must be untouched, and every verdict must be exactly where it was.
        stripped = {
            lm: {c: px for c, px in views.items() if not c.endswith("_again")}
            for lm, views in ext_obs.items()
        }
        assert stripped == base_obs, f"{s.name}: reobserve changed the observation map"
        base = evaluate(s.true_world, list(s.cameras), SMALL_GRID)
        extended = evaluate(s.true_world, list(s.cameras) + list(control.cameras), SMALL_GRID)
        assert [(v.fact, v.supported, v.count) for v in extended.verdicts] == [
            (v.fact, v.supported, v.count) for v in base.verdicts
        ], f"{s.name}: reobserve changed a verdict with zero new information"


def test_pan_action_reports_its_coverage_honestly() -> None:
    """pan_pair moves frames; in top-blind it reveals a top corner and settles height.

    Recorded as a finding, not a control failure: panning adds coverage even
    though it adds no parallax.
    """
    top_blind = next(s for s in scenarios() if s.name == "two-view-top-blind")
    base = evaluate(top_blind.true_world, list(top_blind.cameras), SMALL_GRID)
    assert "height_ft" in base.unknown_facts
    extended = evaluate(
        top_blind.true_world,
        list(top_blind.cameras) + list(pan_pair_for(top_blind.cameras).cameras),
        SMALL_GRID,
    )
    assert "height_ft" not in extended.unknown_facts


def test_action_rigs_pin_their_targets() -> None:
    """Each action's extended rig settles the fact it exists to settle, somewhere."""
    from nonlidar_observability.scenarios import ACTIONS

    # Headline result: one view already supports every fact (metric poses put
    # each ground-point ray on the known ground plane), and panning adds nothing.
    one_view = next(s for s in scenarios() if s.name == "one-view")
    base = evaluate(one_view.true_world, list(one_view.cameras), SMALL_GRID)
    assert base.unknown_facts == (), f"one-view left {base.unknown_facts}"

    # tilt_pair settles height in the top-blind scenario.
    top_blind = next(s for s in scenarios() if s.name == "two-view-top-blind")
    base = evaluate(top_blind.true_world, list(top_blind.cameras), SMALL_GRID)
    assert "height_ft" in base.unknown_facts
    extended = evaluate(
        top_blind.true_world,
        list(top_blind.cameras) + list(ACTIONS["tilt_pair"].cameras),
        SMALL_GRID,
    )
    assert "height_ft" not in extended.unknown_facts

    # end_approach settles the right end in the end-blind scenario.
    end_blind = next(s for s in scenarios() if s.name == "two-view-end-blind")
    base = evaluate(end_blind.true_world, list(end_blind.cameras), SMALL_GRID)
    assert "right_end_ft" in base.unknown_facts
    extended = evaluate(
        end_blind.true_world,
        list(end_blind.cameras) + list(ACTIONS["end_approach"].cameras),
        SMALL_GRID,
    )
    assert "right_end_ft" not in extended.unknown_facts

    # stereo_step settles it there too: its wider walk frames the far end, and
    # every other grid value would put it at a different in-frame pixel.
    extended = evaluate(
        end_blind.true_world,
        list(end_blind.cameras) + list(ACTIONS["stereo_step"].cameras),
        SMALL_GRID,
    )
    assert "right_end_ft" not in extended.unknown_facts
