"""Regression tests for the review-hardening pass; each failed before its fix.

Covers: a coverage view's camera_pos_ft as evidence for the passage behind a gap (and its
refusal when the walk can't vouch for it), the meter's error in the outdoor reach, the route
standoff credited to the wall carrying the path, count and clock enforcement while a solve
lists its battery positions, a zip bundle's directory bounded before metadata is parsed, the
meter working space's depth citation, the battery height's citation when it decides backing,
the reason a rejection-forbidden policy gives, and the route length labelled a lower bound. A
second batch adds independent witnesses -- a gap at a corner, a split span with only one
camera-backed half, the route's lower bound reported with its unknown detour -- and schema
compatibility checked both ways (new scenes and fieldless old results).
"""

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from helpers import at_start, check, observed_band, parsed, rect, run, shared_fixture
from shapely.geometry import Point

import api
from rules import deep_merge, public_rules_dict, rules_from_dict
from scene import SceneError, parse_scene
from solver import FAIL, MAX_STARTS, PASS, UNSURE, SceneTooComplex, Solver

SERVER = Path(__file__).resolve().parents[1]
SCHEMA = SERVER / "schemas"
# Public values with automatic decisions on, so a wrong pass shows as "pass".
PUBLIC = rules_from_dict(
    deep_merge(public_rules_dict(), {"policy": {"id": "test", "auto_approve": True}})
)


def keyframe(kid: str, x: float, z: float) -> dict[str, Any]:
    """A keyframe whose camera stands at plan (x, z), 4.5 ft up."""
    return {
        "id": kid,
        "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, 4.5, z, 1],
        "intrinsics": [1450.0, 1450.0, 960.0, 720.0],
        "w": 1920,
        "h": 1440,
        "img": f"{kid}.jpg",
    }


def passage_scene() -> dict[str, Any]:
    """Two walls with a gap between them (s 10 to 14), a pool just behind the gap's line, and
    ground seen in three spans: along each wall and once across the gap."""
    raw = shared_fixture()
    raw["walls"] = [
        {"id": "w1", "baseline": [[-40, 0], [10, 0]], "height_ft": 9, "plus_minus_ft": 0},
        {"id": "w2", "baseline": [[14, 0], [40, 0]], "height_ft": 9, "plus_minus_ft": 0},
    ]
    raw["overheads"] = [
        {"wall_id": "w1", "span_ft": [-40, 10], "clearance_ft": 9, "plus_minus_ft": 0},
        {"wall_id": "w2", "span_ft": [14, 40], "clearance_ft": 9, "plus_minus_ft": 0},
    ]
    raw["facing"] = [
        {"wall_id": "w1", "span_ft": [-40, 10], "depth_ft": 9, "plus_minus_ft": 0},
        {"wall_id": "w2", "span_ft": [14, 40], "depth_ft": 9, "plus_minus_ft": 0},
    ]
    observed_band(raw, "wall", [(-40, 10), (14, 40)])
    observed_band(raw, "ground", [(-40, 10), (14, 40)], out=30)
    observed_band(raw, "overhead", [(-40, 10), (14, 40)])
    observed_band(raw, "facing", [(-40, 10), (14, 40)])
    raw["objects"] = [
        {
            "type": "pool",
            "wall_id": "w1",
            "span_ft": [11, 13],
            "source": "tap",
            "plus_minus_ft": 0,
            "footprint": rect(11, 13, -12, -11),  # far behind the gap, past the 10 ft rule
        }
    ]
    return raw


# --- 1. a ground span within a gap does not claim the passage by itself -------------------------


def test_a_span_alone_does_not_claim_the_passage() -> None:
    raw = passage_scene()
    observed_band(raw, "ground", [(-40, 10), (10, 14), (14, 40)], out=30)
    c = at_start(raw, 1.5, "pool_clearance", PUBLIC)
    assert c.outcome == UNSURE, c.reason
    assert c.unsure_cause == "unobserved"


def test_a_camera_position_declared_without_keyframes_claims_the_passage() -> None:
    raw = passage_scene()
    observed_band(
        raw,
        "ground",
        [(-40, 10), (10, 14), (14, 40)],
        out=30,
    )
    raw["coverage"]["observed"][-2]["camera_pos_ft"] = [12.0, 1.0]
    c = at_start(raw, 1.5, "pool_clearance", PUBLIC)
    assert c.outcome == PASS, c.reason


def test_a_camera_position_near_a_kept_view_claims_the_passage() -> None:
    raw = passage_scene()
    observed_band(raw, "ground", [(-40, 10), (10, 14), (14, 40)], out=30)
    raw["coverage"]["observed"][-2]["camera_pos_ft"] = [12.0, 1.0]
    raw["keyframes"] = [keyframe("k1", -20.0, 2.0), keyframe("k2", 12.0, 1.5)]
    c = at_start(raw, 1.5, "pool_clearance", PUBLIC)
    assert c.outcome == PASS, c.reason


def test_a_camera_position_off_the_walk_is_refused() -> None:
    raw = passage_scene()
    observed_band(raw, "ground", [(-40, 10), (10, 14), (14, 40)], out=30)
    raw["coverage"]["observed"][-2]["camera_pos_ft"] = [12.0, 1.0]
    # Two keyframes, both far from the opening: nothing vouches for a camera at (12, 1).
    raw["keyframes"] = [keyframe("k1", -20.0, 2.0), keyframe("k2", 30.0, 2.0)]
    with pytest.raises(SceneError, match="camera_pos_ft"):
        parse_scene(raw, PUBLIC.rules)


def test_wall_band_entries_may_carry_a_camera_position() -> None:
    raw = passage_scene()
    raw["coverage"]["observed"][0]["camera_pos_ft"] = [-20.0, 2.0]
    parse_scene(raw, PUBLIC.rules)


# --- 2. the meter's error in the outdoor reach --------------------------------------------------


def test_the_reach_counts_the_meters_error() -> None:
    raw = shared_fixture()
    base = parse_scene(raw, PUBLIC.rules).reach_ft
    wider = parse_scene(
        {**raw, "meter": {**raw["meter"], "plus_minus_ft": 2.0}}, PUBLIC.rules
    ).reach_ft
    assert wider == pytest.approx(base + 2.0)

    # A pool past the reach the old formula computes, with ground seen stopping just short of
    # it: under the old reach the pool's ground is never modelled, so nothing within the
    # pool-clearance radius is unseen and the spot passes; with the meter's error the model
    # reaches past what was seen, and the spot asks for a view.
    pool = {
        "type": "pool",
        "wall_id": "w1",
        "span_ft": [0, 8],
        "source": "tap",
        "plus_minus_ft": 0,
        "footprint": rect(0, 8, base + 3, base + 3.5),
    }
    seen = {**raw, "objects": [pool]}
    observed_band(seen, "ground", [(-40, 40)], out=base + 0.5)
    before = at_start(seen, -20.0, "pool_clearance", PUBLIC)
    assert before.outcome == PASS, before.reason
    with_err = {**seen, "meter": {**seen["meter"], "plus_minus_ft": 2.0}}
    after = at_start(with_err, -20.0, "pool_clearance", PUBLIC)
    assert after.outcome == UNSURE and after.unsure_cause == "unobserved", after.reason


# --- 3. the route standoff credits the wall carrying the path -----------------------------------


def test_the_route_standoff_credits_the_wall_carrying_the_path() -> None:
    raw = shared_fixture()
    raw["walls"] = [
        {"id": "w1", "baseline": [[0, 0], [20, 0]], "height_ft": 9, "plus_minus_ft": 1.0},
        {"id": "w2", "baseline": [[20, 0], [20, -30]], "height_ft": 9, "plus_minus_ft": 0.1},
    ]
    # An exact elec box 0.5 ft off the w1 stretch of the path, with no recorded top: the detour
    # around it is unknown. The path runs along w1 there, whose error is 1.0 ft, so the box
    # counts as in the way; crediting the battery's wall's error (0.1) let it pass.
    raw["objects"] = [
        {
            "type": "elec_box",
            "wall_id": "w1",
            "span_ft": [10, 11],
            "bottom_ft": 4,
            "source": "tap",
            "plus_minus_ft": 0,
            "footprint": rect(10, 11, 0.5, 1.5),
        }
    ]
    scene = parsed(raw, PUBLIC)
    candidate = scene.walls[-1]  # the w2 piece, past the corner
    solver = Solver(scene, PUBLIC)
    # Any battery position on w2: the path always runs along w1 past the box first.
    cands = [solver.evaluate(candidate, s) for s in solver.starts(candidate)]
    paths = [c for cand in cands for c in cand.checks if c.id == "route_path"]
    assert paths and all(p.outcome == UNSURE for p in paths), [p.reason for p in paths]
    assert paths[0].unsure_cause == "unknown_attribute", paths[0].reason


def test_the_route_length_is_labelled_a_lower_bound_when_heights_are_unknown() -> None:
    raw = shared_fixture()
    raw["objects"] = [
        {
            "type": "elec_box",
            "wall_id": "w1",
            "span_ft": [2, 3],
            "bottom_ft": 4,
            "source": "tap",
            "plus_minus_ft": 0,
        }
    ]
    result = run(raw, PUBLIC)
    assert result["route"]["length_is_lower_bound"] is True
    clean = run(shared_fixture(), PUBLIC)
    assert clean["route"]["length_is_lower_bound"] is False


# --- 4. count and clock enforced while positions are generated ----------------------------------


def test_start_generation_enforces_the_count_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    scene = parse_scene(shared_fixture(), PUBLIC.rules)
    solver = Solver(scene, PUBLIC)
    evaluated: list[int] = []
    monkeypatch.setattr(Solver, "starts", lambda self, piece: [0.0] * (MAX_STARTS + 10_000))
    monkeypatch.setattr(Solver, "evaluate", lambda self, piece, s0: evaluated.append(1))
    with pytest.raises(SceneTooComplex, match="more than"):
        solver.candidates(30.0)
    assert not evaluated


def test_start_generation_stops_on_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    scene = parse_scene(shared_fixture(), PUBLIC.rules)
    now = 0.0

    def slow_starts(self: Solver, piece: Any) -> list[float]:
        nonlocal now
        now += 9.0  # each wall's listing alone busts the budget below
        return []

    monkeypatch.setattr(Solver, "starts", slow_starts)
    solver = Solver(scene, PUBLIC, clock=lambda: now)
    with pytest.raises(SceneTooComplex, match="listing the scene's battery positions"):
        solver.candidates(8.0)

    quick = Solver(scene, PUBLIC, clock=lambda: 0.0)
    assert quick.candidates(30.0) == []


# --- 5. a bundle's zip directory bounded before metadata is parsed ------------------------------


def zip_bytes(names: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, b"x")
    return buf.getvalue()


def test_a_bundle_with_too_many_entries_is_refused_before_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = zip_bytes(["scene.json", *(f"f{i}.jpg" for i in range(5))])
    monkeypatch.setattr(api, "MAX_ZIP_ENTRIES", 3, raising=False)
    with pytest.raises(api.ApiError, match="entries"):
        api._open_bundle(data)


def test_a_bundle_without_a_zip_end_record_is_unreadable() -> None:
    # The refusal itself predates this change; what is pinned here is that the tightened
    # bundle open keeps the same 400 unreadable_zip answer for bytes with no end record.
    with pytest.raises(api.ApiError) as e:
        api._open_bundle(b"not a zip at all")
    assert e.value.status == 400 and e.value.code == "unreadable_zip"


def test_a_normal_bundle_still_opens() -> None:
    scene_bytes, names, prefix = api._open_bundle(zip_bytes(["scene.json", "k1.jpg", "sub/k2.jpg"]))
    assert scene_bytes == b"x"
    assert names == {"scene.json", "k1.jpg", "sub/k2.jpg"}
    assert prefix == ""


# --- 6. rules, citations and reasons ------------------------------------------------------------


def test_zero_workspace_rules_are_refused() -> None:
    for field in ("width_ft", "depth_ft"):
        rules = deep_merge(
            public_rules_dict(),
            {"meter_working_space": {field: {"value": 0, "source": "test"}}},
        )
        with pytest.raises(Exception, match=f"meter_working_space.{field} must be positive"):
            rules_from_dict(rules)


def test_meter_space_cites_depth() -> None:
    c = at_start(shared_fixture(), 2.71, "meter_working_space", PUBLIC)
    assert "meter_working_space.depth_ft" in c.rule_keys()


def test_backing_cites_height_when_it_decides() -> None:
    raw = shared_fixture()
    raw["walls"][0]["height_ft"] = 1.0  # surely below the battery's height
    c = at_start(raw, 2.0, "wall_backing", PUBLIC)
    assert c.outcome == FAIL, c.reason
    assert c.rule_key == "battery.height_ft"
    assert c.rule is not None and c.measured == 1.0 and c.threshold == c.rule.value


def test_a_rejection_under_review_only_rules_says_why() -> None:
    rules = rules_from_dict(
        deep_merge(
            public_rules_dict(),
            {"policy": {"id": "test", "auto_approve": True, "allow_reject": False}},
        )
    )
    raw = shared_fixture()
    raw["walls"] = [
        {"id": "w1", "baseline": [[0, 0], [2, 0]], "height_ft": 9, "plus_minus_ft": 0}
    ]  # no stretch as wide as the battery: every reason for a rejection, none allowed
    result = run(raw, rules)
    assert result["decision"] == "manual_review"
    assert any(r["code"] == "policy_review_before_reject" for r in result["reasons"])
    assert result["policy"]["allow_reject"] is False


def test_results_still_validate_against_the_schema() -> None:
    result = run(shared_fixture(), PUBLIC)
    schema = json.loads((SCHEMA / "result.schema.json").read_text())
    jsonschema.validate(result, schema)


# --- 7. independent corner, ground and uncertainty witnesses ------------------------------------


def corner_passage_scene() -> dict[str, Any]:
    """A gap mid-wall, then an outside corner 6 ft past it where the chain turns up the yard.
    The passage behind the gap's line is no wall's behind-strip, so it is yard; a pool sits
    deep behind the gap, past the pool-clearance rule."""
    raw = shared_fixture()
    raw["walls"] = [
        {"id": "w1a", "baseline": [[0, 0], [10, 0]], "height_ft": 9, "plus_minus_ft": 0},
        {"id": "w1b", "baseline": [[14, 0], [20, 0]], "height_ft": 9, "plus_minus_ft": 0},
        {"id": "w2", "baseline": [[20, 0], [20, 30]], "height_ft": 9, "plus_minus_ft": 0},
    ]
    raw["overheads"] = []
    raw["facing"] = []
    raw["meter"]["wall_id"] = "w1a"
    observed_band(raw, "wall", [(-50, 70)])
    observed_band(raw, "ground", [(-50, 70)], out=30)
    observed_band(raw, "overhead", [(-50, 70)])
    observed_band(raw, "facing", [(-50, 70)])
    raw["objects"] = [
        {
            "type": "pool",
            "wall_id": "w1a",
            "span_ft": [11, 13],
            "source": "tap",
            "plus_minus_ft": 0,
            "footprint": rect(11, 13, -12, -11),
        }
    ]
    return raw


def test_a_camera_position_at_a_corner_gap_claims_its_passage() -> None:
    raw = corner_passage_scene()
    observed_band(raw, "ground", [(-50, 70)], out=30)
    c = at_start(raw, 1.5, "pool_clearance", PUBLIC)
    assert c.outcome == UNSURE and c.unsure_cause == "unobserved", c.reason
    # The camera stands in the opening; its s resolves by projection past w1's end, at the
    # corner the gap runs into.
    raw["coverage"]["observed"].append(
        {"band": "ground", "span_ft": [10, 14], "out_ft": 30, "camera_pos_ft": [12.0, 1.0]}
    )
    c = at_start(raw, 1.5, "pool_clearance", PUBLIC)
    assert c.outcome == PASS, c.reason


def test_only_the_camera_backed_part_of_a_split_gap_span_is_claimed() -> None:
    raw = passage_scene()
    observed_band(raw, "ground", [(-40, 10), (10, 12), (12, 14), (14, 40)], out=30)
    raw["coverage"]["observed"][-3]["camera_pos_ft"] = [11.0, 1.0]  # only [10, 12] claims
    scene = parsed(raw, PUBLIC)
    unseen = scene.unobserved_ground()
    assert not unseen.intersects(Point(11.0, -1.0)), "the camera-backed half must be seen"
    assert unseen.intersects(Point(13.0, -1.0)), "the other half is still nobody's view"


def test_the_route_reports_its_lower_bound_with_the_unknown_detour() -> None:
    raw = shared_fixture()
    raw["objects"] = [
        {
            "type": "elec_box",
            "wall_id": "w1",
            "span_ft": [2, 3],
            "bottom_ft": 4,
            "source": "tap",
            "plus_minus_ft": 0,
        }
    ]
    result = run(raw, PUBLIC)
    path = check(result, "route_path")
    assert (path["outcome"], path["unsure_cause"]) == ("unsure", "unknown_attribute"), path[
        "reason"
    ]
    assert result["route"]["length_is_lower_bound"] is True
    assert result["decision"] == "manual_review"


# --- 8. schema compatibility, both ways ----------------------------------------------------------


def test_scenes_with_and_without_camera_positions_validate_against_the_schema() -> None:
    schema = json.loads((SCHEMA / "scene.schema.json").read_text())
    jsonschema.validate(shared_fixture(), schema)  # the old shape, keyframes or none
    jsonschema.validate(corner_passage_scene(), schema)
    raw = passage_scene()
    observed_band(raw, "ground", [(-40, 10), (10, 14), (14, 40)], out=30)
    raw["coverage"]["observed"][-2]["camera_pos_ft"] = [12.0, 1.0]
    jsonschema.validate(raw, schema)  # the new field on a ground view


def test_results_without_the_new_fields_still_validate() -> None:
    # Old results are pre-change output, not errors: the new fields are optional in the schema.
    schema = json.loads((SCHEMA / "result.schema.json").read_text())
    stripped = json.loads(json.dumps(run(shared_fixture(), PUBLIC)))
    del stripped["route"]["length_is_lower_bound"]
    del stripped["policy"]["allow_reject"]
    jsonschema.validate(stripped, schema)
