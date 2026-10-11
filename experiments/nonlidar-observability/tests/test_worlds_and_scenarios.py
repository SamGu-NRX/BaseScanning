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
        "one-view-tops-only": {
            **{k: [] for k in LANDMARK_IDS},
            "top_left": ["kf0"],
            "top_right": ["kf0"],
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


def test_tops_only_facts_are_unknown_through_a_continuous_family() -> None:
    """Tops-only: grid count 1, but a continuous family shares the observation.

    Both top corners sit on fixed rays from the single camera center, and the
    equal-height constraint is scale-homogeneous: the family scales the wall
    about the camera center, moving distance, both ends, and height while
    orientation stays fixed. So orientation_deg is supported even from this
    rig, and the other four facts are unknown despite the grid agreeing.
    """
    tops_only = next(s for s in scenarios() if s.name == "one-view-tops-only")
    base = evaluate(tops_only.true_world, list(tops_only.cameras), SMALL_GRID)
    assert base.compatible_count == 1
    orientation = next(v for v in base.verdicts if v.fact == "orientation_deg")
    assert orientation.supported and orientation.family_pair is None
    for fact in ("distance_ft", "left_end_ft", "right_end_ft", "height_ft"):
        v = next(x for x in base.verdicts if x.fact == fact)
        assert v.family_pair is not None, f"{fact}: family not detected"
        assert not v.supported
        assert v.family_pair[0] != v.family_pair[1]


def test_tops_only_family_member_reproduces_the_observation() -> None:
    """Independent of the probe: an analytically built family member is hash-equal.

    Scales both top corners along their rays from the camera center by the
    same factor (preserving equal heights) and checks that the probe walks to
    a member sharing the observation, and that orientation is not movable.
    """
    import numpy as np

    from nonlidar_observability.identifiability import probe_family
    from nonlidar_observability.observe import observation_hash, observe

    tops_only = next(s for s in scenarios() if s.name == "one-view-tops-only")
    cams = list(tops_only.cameras)
    true_obs = observe(tops_only.true_world, cams)
    c = cams[0]
    scale = 1.08
    corners = []
    for lm in ("top_left", "top_right"):
        px = np.array(true_obs[lm]["kf0"])
        x_cam = (px[0] - c.cx) / c.fx
        y_cam = (px[1] - c.cy) / c.fy
        ray = c.rotation @ np.array([x_cam, y_cam, -1.0])
        corners.append(c.center + scale * (tops_only.true_world.h_ft - c.center[1]) / ray[1] * ray)
    assert np.isclose(corners[0][1], corners[1][1])

    found = probe_family(tops_only.true_world, cams, true_obs)
    assert found, "probe found no family member at all"
    assert "theta_deg" not in found, "orientation should stay pinned along the family"
    assert {"d_ft", "s0_ft", "s1_ft", "h_ft"} <= set(found)
    member = found["d_ft"]
    assert abs(member.d_ft - tops_only.true_world.d_ft) > 0.05
    assert observation_hash(observe(member, cams)) == observation_hash(true_obs)


def test_tops_only_actions_settle_the_family() -> None:
    """The observability comparison: which actions settle the family's facts.

    Distance, both ends, and height are unknown on the tops-only rig. Any
    action adding a distinct camera center (tilt_pair, stereo_step,
    end_approach) settles every fact; pan_pair moves frames without a new
    center and the reobserve control adds nothing, so the family survives
    both. Orientation is supported throughout.
    """
    tops_only = next(s for s in scenarios() if s.name == "one-view-tops-only")
    run = tops_only.run()
    family_facts = ("distance_ft", "left_end_ft", "right_end_ft", "height_ft")
    for fact in family_facts:
        assert not next(v for v in run["verdicts"] if v["fact"] == fact)["supported"]
    assert next(v for v in run["verdicts"] if v["fact"] == "orientation_deg")["supported"]
    post = {name: a["post_verdicts"] for name, a in run["actions"].items()}
    for fact in family_facts:
        assert post["tilt_pair"][fact]["supported"]
        assert post["stereo_step"][fact]["supported"]
        assert post["end_approach"][fact]["supported"]
        assert not post["pan_pair"][fact]["supported"]
        assert not post["reobserve"][fact]["supported"]
    for name in ("tilt_pair", "stereo_step", "end_approach", "pan_pair", "reobserve"):
        assert post[name]["orientation_deg"]["supported"]


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
