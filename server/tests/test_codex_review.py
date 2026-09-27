"""Regression tests for the Codex review of #11 at 6c7ca23; each failed before its fix."""

from pathlib import Path

import pytest
from helpers import at_start, observed_band, parsed, rect, shared_fixture
from test_s4_round import PUBLIC, answer

from rules import deep_merge, load_rules, public_rules_dict, rules_from_dict
from solver import FAIL, PASS, UNSURE

W = 31 / 12


# --- 1. a declared wall height ---------------------------------------------------------------


def test_a_wall_lower_than_the_battery_fails_the_backing() -> None:
    # Before: height_ft was dropped, so a 2 ft wall passed a 3.29 ft battery.
    raw = shared_fixture()
    raw["walls"][0]["height_ft"] = 2
    raw["meter"]["pos"][1] = 1
    assert at_start(raw, 6.0, "wall_backing", PUBLIC).outcome == FAIL


def test_a_tall_enough_or_undeclared_wall_is_unchanged() -> None:
    assert at_start(shared_fixture(), 6.0, "wall_backing", PUBLIC).outcome == PASS  # 9 ft
    raw = shared_fixture()
    del raw["walls"][0]["height_ft"]
    assert at_start(raw, 6.0, "wall_backing", PUBLIC).outcome == PASS


# --- 2. facing and headroom coverage over every position the battery may have ------------------


def seen_only_over_the_nominal_spot() -> dict:
    raw = shared_fixture()
    raw["walls"][0]["plus_minus_ft"] = 0.5
    raw["ground"] = [{"type": "lawn", "polygon": rect(-40, 40, 0, 30), "plus_minus_ft": 0}]
    raw["facing"], raw["overheads"] = [], []
    for band in ("facing", "overhead"):
        observed_band(raw, band, [])
        raw["coverage"]["observed"].append({"band": band, "span_ft": [6, 6 + W], "out_ft": 9})
    return raw


def test_views_of_only_the_nominal_stretch_do_not_settle_facing_or_headroom() -> None:
    # Before: coverage of exactly [6, 6 + W] passed, though the battery may sit 0.5 ft either way.
    raw = seen_only_over_the_nominal_spot()
    for check_id in ("facing_gap", "headroom"):
        check = at_start(raw, 6.0, check_id, PUBLIC)
        assert (check.outcome, check.unsure_cause) == (UNSURE, "unobserved"), check_id


def test_a_low_overhang_within_the_error_is_unsure() -> None:
    raw = seen_only_over_the_nominal_spot()
    raw["overheads"] = [
        {"wall_id": "w1", "span_ft": [5.6, 5.9], "clearance_ft": 2, "plus_minus_ft": 0}
    ]
    observed_band(raw, "overhead", [(-40, 40)])
    assert at_start(raw, 6.0, "headroom", PUBLIC).outcome == UNSURE


# --- 3. a real gap between walls ------------------------------------------------------------------


def two_walls(error: float) -> dict:
    raw = shared_fixture()
    raw["walls"] = [
        {"id": "w1", "baseline": [[-40, 0], [3, 0]], "height_ft": 9, "plus_minus_ft": error},
        {"id": "w2", "baseline": [[3.5, 0], [40, 0]], "height_ft": 9, "plus_minus_ft": error},
    ]
    return raw


def test_a_gap_larger_than_the_walls_errors_is_a_gap() -> None:
    # Before: exact walls 0.5 ft apart were joined (the tolerance is 0.6), the route passed and
    # s skipped the gap.
    scene = parsed(two_walls(0.0), PUBLIC)
    assert [(p.s0, p.s1) for p in scene.gaps] == [(3.0, 3.5)]
    assert scene.s_max == pytest.approx(40.0)  # s counts the gap
    assert at_start(two_walls(0.0), 6.0, "route_path", PUBLIC).outcome == FAIL


def test_a_gap_within_the_walls_errors_is_a_join() -> None:
    scene = parsed(two_walls(0.3), PUBLIC)  # 0.5 <= 0.3 + 0.3, under the 0.6 cap
    assert scene.gaps == []
    assert scene.s_max == pytest.approx(39.5)  # the walls meet; s continues without the gap


# --- 4. the spot's wall and segment belong together ---------------------------------------------


def test_a_spot_on_the_second_of_two_joined_walls_names_its_own_segment() -> None:
    # Before: wall_id w2 with segment 1, the first wall's index; w2 has only segment 0.
    raw = shared_fixture()
    raw["walls"] = [
        {
            "id": "w1",
            "baseline": [[-40, -10], [-40, 0], [4, 0]],
            "height_ft": 9,
            "plus_minus_ft": 0,
        },
        {"id": "w2", "baseline": [[4, 0], [40, 0]], "height_ft": 9, "plus_minus_ft": 0},
    ]
    spot = answer(raw)["spot"]
    assert (spot["wall_id"], spot["segment"]) == ("w2", 0)
    runs = {(r["wall_id"], r["segment"]) for r in answer(raw)["sweep"]}
    assert ("w2", 1) not in runs


# --- 5. a zero sweep step -------------------------------------------------------------------------


def test_a_zero_sweep_step_is_refused_when_the_rules_load() -> None:
    # Before: it validated, and every solve then divided by zero.
    data = deep_merge(public_rules_dict(), {"sweep": {"step_ft": {"value": 0.0}}})
    with pytest.raises(ValueError, match=r"sweep\.step_ft"):
        rules_from_dict(data)


# --- 6. placeholders under partial private rules --------------------------------------------------


def test_a_partial_private_policy_names_the_checks_still_on_placeholders(tmp_path: Path) -> None:
    # Before: any private file cleared the notice, though the pool and driveway checks still ran
    # on public placeholder values.
    private = tmp_path / "rules.yaml"
    private.write_text(
        "policy: {id: p, version: '1', auto_approve: true, allow_reject: true}\n"
        "clearances:\n  gas_ft: {value: 3.0, source: 'test'}\n"
    )
    notice = load_rules(private).rules.policy.notice
    assert notice is not None
    for check_id in ("pool_clearance", "drive_clearance", "route_length", "headroom"):
        assert check_id in notice, notice
    assert "gas_clearance" not in notice


def test_the_public_demo_notice_is_unchanged() -> None:
    assert "not Base's" in (PUBLIC.rules.policy.notice or "")


# --- follow-ups from the caretaker's review of 903d86f ----------------------------------------


def two_heights() -> dict:
    """Collinear walls: w1 up to the meter, 2 ft tall; w2 from it, 9 ft. Joined into one piece."""
    raw = shared_fixture()
    raw["meter"]["pos"][1] = 1
    raw["walls"] = [
        {"id": "w1", "baseline": [[-40, 0], [0, 0]], "height_ft": 2, "plus_minus_ft": 0},
        {"id": "w2", "baseline": [[0, 0], [40, 0]], "height_ft": 9, "plus_minus_ft": 0},
    ]
    return raw


def test_a_battery_on_the_taller_wall_uses_that_walls_height() -> None:
    # Before: the joined piece kept the lower height everywhere, so a spot wholly on the 9 ft
    # wall failed on 2 ft and the scene was rejected.
    assert at_start(two_heights(), 6.0, "wall_backing", PUBLIC).outcome == PASS
    assert answer(two_heights())["decision"] != "reject"


def test_a_battery_across_the_join_uses_the_lower_height() -> None:
    assert at_start(two_heights(), -1.0, "wall_backing", PUBLIC).outcome == FAIL


def test_segments_are_numbered_as_uploaded() -> None:
    # Before: collinear points were merged first, so the second uploaded segment reported 0.
    raw = shared_fixture()
    raw["walls"][0]["baseline"] = [[-40, 0], [0, 0], [40, 0]]
    scene = parsed(raw, PUBLIC)
    assert scene.segment_at(7.3) == ("w1", 1)
    assert scene.segment_at(-7.3) == ("w1", 0)
    assert scene.segment_at(0.0) in {("w1", 0), ("w1", 1)}  # on the boundary: either
    spot = answer(raw)["spot"]
    assert (spot["wall_id"], spot["segment"]) == ("w1", 1)


@pytest.mark.parametrize("gap", [0.005, 0.010001])
def test_a_known_gap_between_exact_walls_stays_a_gap(gap: float) -> None:
    # Before: exact walls 0.005 ft apart were joined by the 0.01 ft coverage floor.
    raw = two_walls(0.0)
    raw["walls"][0]["baseline"] = [[-40, 0], [3, 0]]
    raw["walls"][1]["baseline"] = [[3 + gap, 0], [40, 0]]
    scene = parsed(raw, PUBLIC)
    assert len(scene.gaps) == 1
    assert at_start(raw, 6.0, "route_path", PUBLIC).outcome == FAIL


def test_walls_sharing_an_endpoint_still_meet() -> None:
    raw = two_walls(0.0)
    raw["walls"][1]["baseline"] = [[3, 0], [40, 0]]
    assert parsed(raw, PUBLIC).gaps == []
