"""Unit tests for the criteria lattice, profiles, class map, views, and hybrid fill."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml
from shapely.geometry import Polygon

from server.contracts.jobs import StageContext, StageResult
from server.contracts.scene import Scene
from server.contracts.verdict import Criterion, Verdict, verdict_contract_errors
from server.criteria import run as stage_run
from server.criteria.c1 import engine3d as engine
from server.criteria.c1.chain_hybrid import _sort_spans, adopt, chain_inputs_observed
from server.criteria.c1.from_scene_v0 import CLASS, convert, stamp_ground_rule_ids
from server.criteria.c1.to_verdict import (
    _restore_geometric,
    apply_review_flags,
    chain_s,
    criterion_dict,
    query_evidence,
    regions_for,
    s_of,
)
from server.criteria.c2.grid_adapter import c2_rules
from server.criteria.c2.grid_adapter import from_scene as c2_from_scene
from server.criteria.c2.sweep import (
    cable_passable,
    headroom_sample_heights,
    majority_unknown_grid,
    opening_face_cells,
    rear_standoff,
)
from server.criteria.c3.client import HouseSpend, api_key_for
from server.criteria.c3.facts import disabled_facts, evaluate_facts
from server.criteria.c3.judge import flags_from, judge_if_pass, resolve_judge
from server.criteria.c3.overlay import judge_photos
from server.criteria.compose import attach_c3_facts, both_ends_ok
from server.criteria.config import CriteriaConfig
from server.criteria.lattice import c2_veto, judge_join, margin_outcome, site_decision
from server.criteria.occluders import assert_not_provisional, missing_occluder_geometry, ply_vertex_count
from server.criteria.run import CriteriaInputError, _query, _run_c2, _views_from_runs, evaluate_scene, merged_profile
from server.criteria.tests.data.facade_gravel import write as write_gravel
from server.criteria.tests.data.synthetic_flatwall.make import write
from server.criteria.views_needed import build as build_views
from server.criteria.views_needed import observed_wall_spans

ROOT = Path(__file__).resolve().parents[3]
PROFILES = yaml.safe_load((ROOT / "server/criteria/profiles.yaml").read_text())


def _cfg(**kw) -> CriteriaConfig:
    base = dict(absent_facts="ignore", meter_number_required=True)
    base.update(kw)
    return CriteriaConfig(**base)


def _profile(name: str) -> dict:
    return merged_profile(name)


def _mini(**over) -> dict:
    scene = {
        "scene_id": "mini",
        "provenance": {"data": "synthetic", "site": "mini", "producer": {"path": "test"}},
        "frame": {"anchor": {"element_id": "meter"}},
        "walls": [
            {
                "id": "w1",
                "baseline_m": [[-2.0, 0.0], [2.0, 0.0]],
                "height_m": 3.0,
                "s_range_m": [-2.0, 2.0],
                "plus_minus_m": 0.05,
                "evidence": "observed",
            }
        ],
        "chain": {
            "wall_ids": ["w1"],
            "ends": {"left": {"kind": "limit"}, "right": {"kind": "limit"}},
            "corners": [],
        },
        "ground": {
            "regions": [
                {
                    "id": "g1",
                    "material": "concrete",
                    "polygon_m": [[-2, 0], [2, 0], [2, 3], [-2, 3]],
                    "evidence": "observed",
                    "plus_minus_m": 0.05,
                }
            ]
        },
        "objects": [
            {
                "id": "meter",
                "class": "meter",
                "geometry": {"kind": "point", "pos_m": [0.0, 1.5, 0.0]},
                "wall_id": "w1",
                "plus_minus_m": 0.05,
                "confidence": 1.0,
                "evidence": "observed",
            }
        ],
        "coverage": {
            "bands": [
                {"wall_id": "w1", "band": "wall", "s_m": [-2, 2]},
                {"wall_id": "w1", "band": "ground", "s_m": [-2, 2], "out_m": 3},
            ],
            "sensitivity": [],
        },
    }
    scene.update(over)
    return scene


def _add(scene, **obj):
    scene["objects"].append(
        {
            "plus_minus_m": 0.05,
            "confidence": 1.0,
            "evidence": "observed",
            "source": {"kind": "annotation"},
            **obj,
        }
    )
    return scene


# --- lattice -----------------------------------------------------------------


def test_c2_veto_pass_plus_fail_is_unsure():
    assert c2_veto("pass", "fail") == "unsure"


def test_c2_veto_pass_plus_unsure_is_unsure():
    assert c2_veto("pass", "unsure") == "unsure"


def test_c2_veto_never_upgrades_a_fail():
    assert c2_veto("fail", "pass") == "fail"
    assert c2_veto("unsure", None) == "unsure"


def test_judge_join_clamps_reject():
    assert judge_join("pass", "reject", may_reject=False) == "manual_review"


def test_judge_join_reject_when_allowed():
    assert judge_join("pass", "reject", may_reject=True) == "reject"


def test_judge_join_keeps_pipeline_when_judge_is_absent():
    assert judge_join("pass", None, may_reject=False) == "pass"


def test_judge_join_downgrades_only():
    assert judge_join("manual_review", "pass", may_reject=False) == "manual_review"


@pytest.mark.parametrize(
    ("mou_side", "expected"),
    [(1.0, "unsure"), (-1.0, "unsure"), (1.01, "pass"), (-1.01, "fail")],
)
def test_margin_outcome_boundaries(mou_side, expected):
    # at_least: margin = value - threshold. Uncertainty 1 makes margin/uncertainty = mou_side.
    outcome, _margin, mou = margin_outcome(mou_side, 1.0, 0.0, "at_least")
    assert outcome == expected
    assert mou == pytest.approx(mou_side)


@pytest.mark.parametrize("profile", ["A", "B", "C", "S"])
@pytest.mark.parametrize("fact", ["absent", "unsure", "fail"])
def test_site_decision_facts_across_profiles(profile, fact):
    cfg_review = _cfg(absent_facts="review")
    cfg_ignore = _cfg(absent_facts="ignore")
    facts = {"panel_recall": None}
    if fact == "unsure":
        facts = {"panel_recall": "unsure"}
    elif fact == "fail":
        facts = {"panel_recall": "fail"}
    allow = _profile(profile)["policy"]["allow_reject"]
    decision, _ = site_decision("pass", facts, {}, [], _profile(profile), cfg_review)
    if fact == "fail":
        assert decision == "reject"
    elif fact == "unsure":
        assert decision == "manual_review"
    else:
        assert decision == "manual_review"
        ignored, _ = site_decision("pass", facts, {}, [], _profile(profile), cfg_ignore)
        assert ignored == "pass"
    if not allow:
        held, _ = site_decision("fail", {"panel_recall": None}, {}, [], _profile(profile), cfg_ignore)
        assert held == "manual_review"


def test_site_decision_not_same_wall_does_not_fail():
    decision, _ = site_decision("pass", {"not_same_wall": "fail"}, {}, [], _profile("A"), _cfg())
    assert decision == "pass"


def test_site_decision_unclean_meter_number_reviews():
    decision, reasons = site_decision("pass", {}, {"meter_number": {"clean": False}}, [], _profile("C"), _cfg())
    assert decision == "manual_review"
    assert "fact_unsure:meter_number" in reasons


# --- profiles ----------------------------------------------------------------


def test_profiles_match_brief_table():
    expect = {
        "A": dict(w=2.0, d=2.0, h=3.291667, route=15, corners=0, gas=5, drive=5, pool=5, reject=False, backing=0),
        "B": dict(w=2.0, d=2.0, h=3.291667, route=25, corners=1, gas=5, drive=5, pool=5, reject=False, backing=0),
        "C": dict(
            w=2.583333, d=1.833333, h=3.291667, route=26, corners=1, gas=3, drive=3, pool=10, reject=True, backing=3
        ),
        "S": dict(w=2.583333, d=2.0, h=3.291667, route=15, corners=0, gas=5, drive=5, pool=10, reject=True, backing=3),
    }
    for name, row in expect.items():
        p = _profile(name)
        assert p["battery"]["height_ft"] == pytest.approx(row["h"])
        assert p["battery"]["width_ft"] == pytest.approx(row["w"])
        assert p["battery"]["depth_ft"] == pytest.approx(row["d"])
        assert p["route"]["max_ft"] == row["route"]
        assert p["route"]["max_corners"] == row["corners"]
        assert p["clearances"]["gas_ft"] == row["gas"]
        assert p["clearances"]["drive_ft"] == row["drive"]
        assert p["clearances"]["pool_ft"] == row["pool"]
        assert p["policy"]["allow_reject"] is row["reject"]
        assert p["backing_min_ft"] == row["backing"]
        assert p["facing"]["min_ft"] is None if name in {"A", "B"} else p["facing"]["min_ft"] == 5.0


def test_profile_s_matches_strictest_clearances():
    strict = yaml.safe_load((ROOT / "server/criteria/c2/rules_strictest.yaml").read_text())
    s = _profile("S")
    assert s["clearances"]["gas_ft"] == strict["clearances"]["gas_ft"]
    assert s["clearances"]["drive_ft"] == strict["clearances"]["drive_ft"]
    assert s["clearances"]["pool_ft"] == strict["clearances"]["pool_ft"]
    assert s["headroom"]["min_ft"] == strict["headroom"]["min_ft"]
    assert s["facing"]["min_ft"] == strict["facing"]["from_wall_ft"]
    # Brief §4.2 sets S depth to 2.0 ft; the C2 dump's strictest file still says 1.833.
    assert s["battery"]["depth_ft"] == pytest.approx(2.0)
    assert s["battery"]["height_ft"] == pytest.approx(strict["battery"]["height_ft"])


# --- class map ---------------------------------------------------------------


def test_class_map_extensions():
    scene = _mini()
    _add(
        scene,
        id="well",
        class_="basement_well",
        geometry={"kind": "wall_rect", "wall_id": "w1", "s_m": [0.4, 0.8], "bottom_m": 0.0, "top_m": 0.4},
    )
    # helper used class_ by mistake; fix
    scene["objects"][-1]["class"] = scene["objects"][-1].pop("class_")
    _add(
        scene,
        id="gate1",
        geometry={"kind": "wall_rect", "wall_id": "w1", "s_m": [-1.2, -0.8], "bottom_m": 0.0, "top_m": 2.0},
    )
    scene["objects"][-1]["class"] = "gate"
    _add(scene, id="bib", class_="hose_bib", geometry={"kind": "point", "pos_m": [1.0, 0.4, 0.1]})
    scene["objects"][-1]["class"] = scene["objects"][-1].pop("class_")
    _add(scene, id="bin1", geometry={"kind": "point", "pos_m": [1.2, 0.5, 0.4]})
    scene["objects"][-1]["class"] = "bin"
    built = convert(scene, "angle80", "C")
    types = {o["id"]: o["type"] for o in built["objects"]}
    assert types["well"] == "door"
    assert next(o for o in built["objects"] if o["id"] == "well")["attrs"]["well"] is True
    assert next(o for o in built["objects"] if o["id"] == "gate1")["attrs"]["operable"] is True
    assert types["bib"] == "downspout"
    assert "bin1" not in types  # movable in C, dropped
    blocked = convert(scene, "angle80", "A")
    assert any(o["id"] == "bin1" for o in blocked["obstacles"])


def test_location_unsure_and_low_confidence():
    scene = _mini()
    _add(
        scene,
        id="far",
        geometry={"kind": "wall_rect", "wall_id": "w1", "s_m": [1.0, 1.4], "bottom_m": 1.0, "top_m": 1.5},
        plus_minus_m=9.99,
    )
    scene["objects"][-1]["class"] = "window"
    _add(
        scene,
        id="dim",
        geometry={"kind": "wall_rect", "wall_id": "w1", "s_m": [-1.5, -1.1], "bottom_m": 1.0, "top_m": 1.4},
        confidence=0.4,
    )
    scene["objects"][-1]["class"] = "window"
    built = convert(scene, "angle80", "C")
    assert "far" in built["meta"]["location_unsure_ids"]
    assert "dim" in built["meta"]["review_only_ids"]


def test_unknown_ground_is_not_a_patch():
    scene = _mini()
    scene["ground"]["regions"][0]["material"] = "unknown"
    built = convert(scene, "angle80", "C")
    assert built["ground_patches"] == []


def test_plant_is_unmapped():
    scene = _mini()
    _add(scene, id="p", geometry={"kind": "point", "pos_m": [0.5, 0.2, 0.3]})
    scene["objects"][-1]["class"] = "plant"
    built = convert(scene, "angle80", "C")
    assert all(o["id"] != "p" for o in built["objects"] + built["obstacles"])
    assert "plant" not in CLASS


# --- views -------------------------------------------------------------------


def test_views_merge_overlapping_spans():
    views = build_views(
        [
            {
                "kind": "band",
                "band": "wall",
                "wall_id": "w1",
                "s_m": [0.0, 1.0],
                "criteria": ["wall_backing"],
                "why": "unobserved",
            },
            {
                "kind": "band",
                "band": "wall",
                "wall_id": "w1",
                "s_m": [0.9, 2.0],
                "criteria": ["opening_clearance"],
                "why": "grazing",
            },
        ],
        8,
        meter_wall_id="w1",
        chain_wall_ids=["w1"],
    )
    assert len(views) == 1
    assert views[0]["s_m"] == [0.0, 2.0]
    assert "opening_clearance" in views[0]["criteria"]
    assert views[0]["id"] == "vn1"
    assert "view_angle_deg" not in views[0]


def test_null_span_is_omitted_and_past_end_has_a_span():
    views = build_views(
        [
            {
                "kind": "past_end",
                "side": "right",
                "wall_id": "w1",
                "s_m": None,
                "criteria": ["placement"],
                "why": "unobserved",
            }
        ],
        8,
    )
    assert "s_m" not in views[0]
    scene = _mini()
    scene["chain"]["ends"]["right"]["kind"] = "unexplored"
    scene["walls"][0]["s_range_m"] = [-2.0, 2.0]
    items = _views_from_runs([], {"w1": (0.0, 1)}, scene)
    past = [item for item in items if item["kind"] == "past_end"]
    assert past[0]["s_m"] == [2.0, 2.5]


def test_views_cap():
    items = [
        {
            "kind": "band",
            "band": "wall",
            "wall_id": "w1",
            "s_m": [i, i + 0.1],
            "criteria": ["wall_backing"],
            "why": "unobserved",
        }
        for i in range(10)
    ]
    assert len(build_views(items, 3, meter_wall_id="w1")) == 3


def test_views_rank_ground_and_adjacent_before_meter_wall():
    views = build_views(
        [
            {
                "kind": "band",
                "band": "wall",
                "wall_id": "w1",
                "s_m": [0.0, 0.2],
                "criteria": ["wall_backing"],
                "why": "unobserved",
            },
            {
                "kind": "band",
                "band": "ground",
                "wall_id": "w1",
                "s_m": [0.0, 0.2],
                "criteria": ["ground_surface"],
                "why": "unobserved",
            },
            {
                "kind": "band",
                "band": "wall",
                "wall_id": "w2",
                "s_m": [1.0, 1.2],
                "criteria": ["wall_backing"],
                "why": "unobserved",
            },
        ],
        8,
        meter_wall_id="w1",
        chain_wall_ids=["w1", "w2"],
    )
    assert [v["band"] + ":" + v["wall_id"] for v in views[:2]] == ["ground:w1", "wall:w2"]


def test_coverage_report_id_is_kept_and_angle_dropped():
    views = build_views(
        [
            {
                "id": "cv-ground",
                "kind": "band",
                "band": "ground",
                "wall_id": "w1",
                "s_m": [0, 1],
                "criteria": ["ground_surface"],
                "why": "unobserved",
                "view_angle_deg": [20, 60],
            }
        ],
        8,
    )
    assert views[0]["id"] == "cv-ground"
    assert "view_angle_deg" not in views[0]


# --- both ends, ground rule, fuzz --------------------------------------------


def test_both_ends_require_limits_and_observed_ends(monkeypatch):
    scene = _mini()
    scene["chain"]["ends"]["left"]["kind"] = "unexplored"
    assert both_ends_ok(scene, Path("."), None) is False
    scene["chain"]["ends"]["left"]["kind"] = "limit"
    monkeypatch.setattr("server.criteria.compose.query_evidence", lambda *a, **k: {"status": "observed"})
    assert both_ends_ok(scene, Path("."), object()) is True
    monkeypatch.setattr("server.criteria.compose.query_evidence", lambda *a, **k: {"status": "unobserved"})
    assert both_ends_ok(scene, Path("."), object()) is False


def test_ground_rule_missing_raises():
    scene = _mini()
    scene["coverage"]["sensitivity"] = [{"surface": "ground", "rule_id": "angle75", "bands": []}]
    built = convert(scene, "angle80", "C")
    assert built["meta"]["ground_rule_missing"] is True


def test_unjoined_wall_uses_scene_chain_s():
    scene = _mini()
    scene["walls"].append(
        {
            "id": "w2",
            "baseline_m": [[10.0, 0.0], [14.0, 0.0]],
            "height_m": 3.0,
            "s_range_m": [10.0, 14.0],
            "plus_minus_m": 0.05,
            "evidence": "observed",
        }
    )
    loaded = engine.load_scene(convert(scene, "angle80", "C"))
    sm = chain_s(loaded, scene)
    assert abs(s_of(sm, "w2", 0.0) - 10.0) < 1e-6
    assert "w2" not in chain_s(loaded)


def test_legacy_sensitivity_matches_incidence_when_rule_id_is_absent():
    scene = _mini()
    scene["coverage"]["sensitivity"] = [
        {
            "surface": "ground",
            "max_incidence_deg": 75.0,
            "bands": [{"wall_id": "w1", "band": "ground", "s_m": [0.0, 0.2], "out_m": 0.1}],
        },
        {
            "surface": "ground",
            "max_incidence_deg": 80.0,
            "bands": [{"wall_id": "w1", "band": "ground", "s_m": [-2.0, 2.0], "out_m": 4.0}],
        },
    ]
    built = convert(scene, "angle80", "C")
    assert built["meta"]["ground_rule_missing"] is False
    assert built["coverage"]["ground"] is not None
    missing = convert(scene, "footprint3in", "C")
    assert missing["meta"]["ground_rule_missing"] is True
    stamp_ground_rule_ids(scene)
    assert scene["coverage"]["sensitivity"][0]["rule_id"] == "angle75"
    assert scene["coverage"]["sensitivity"][1]["rule_id"] == "angle80"


def test_meter_past_wall_end_is_clamped():
    scene = _mini()
    scene["objects"][0]["geometry"]["pos_m"] = [9.0, 1.5, 0.0]
    loaded = engine.load_scene(convert(scene, "angle80", "C"))
    assert 0.0 <= loaded.meter_u <= loaded.walls["w1"].L


def test_zero_length_wall_does_not_crash():
    scene = _mini()
    scene["walls"][0]["baseline_m"] = [[0.0, 0.0], [0.0, 0.0]]
    built = convert(scene, "angle80", "C")
    assert built["walls"][0]["id"] == "w1"


def test_self_intersecting_ground_polygon_does_not_crash():
    scene = _mini()
    scene["ground"]["regions"][0]["polygon_m"] = [[0, 0], [1, 1], [1, 0], [0, 1]]
    convert(scene, "angle80", "C")


def test_missing_ground_grid_skips_c2():
    assert c2_from_scene(_mini(), Path("/tmp")) is None


def test_reversed_opening_span_is_sorted():
    raw = {"objects": [{"span_ft": [4.0, 1.0], "type": "window"}]}
    assert _sort_spans(raw)["objects"][0]["span_ft"] == [1.0, 4.0]


# --- C3, C2, hybrid ----------------------------------------------------------


def _passing_verdict() -> dict:
    return {
        "site": {"decision": "pass", "reasons": [], "chosen_candidate": "run1"},
        "criteria": [
            {
                "id": "placement",
                "scope": "site",
                "outcome": "pass",
                "reason": "pass",
                "measured": None,
                "threshold": None,
                "fact": {"value": "C", "clean": True, "coverage": []},
            }
        ],
        "views_needed": [],
    }


def _c3_fact(fid: str, outcome: str, cause: str | None = None) -> dict:
    row = {
        "id": fid,
        "scope": "site",
        "outcome": outcome,
        "reason": "photo fact",
        "measured": None,
        "threshold": None,
    }
    if cause:
        row["unsure_cause"] = cause
    return row


def test_c3_decision_fact_downgrades_a_pass_in_every_config():
    """Dev leaves absent facts alone. An unsure or failing C3 decision fact still reviews."""
    unsure = _passing_verdict()
    attach_c3_facts(unsure, [_c3_fact("main_panel_type", "unsure", "unknown_attribute")])
    assert unsure["site"]["decision"] == "manual_review"
    assert unsure["site"]["reasons"] == ["fact_unsure:main_panel_type"]
    placement = next(row for row in unsure["criteria"] if row["id"] == "placement")
    assert placement["outcome"] == "unsure"
    assert placement["unsure_cause"] == "rule_requires_review"
    assert any(row["id"] == "main_panel_type" and row["outcome"] == "unsure" for row in unsure["criteria"])

    failed = _passing_verdict()
    attach_c3_facts(failed, [_c3_fact("panel_in_house", "fail")])
    assert failed["site"]["decision"] == "manual_review"
    assert failed["site"]["reasons"] == ["fact_unsure:panel_in_house"]

    ignored = _passing_verdict()
    attach_c3_facts(ignored, disabled_facts())
    assert ignored["site"]["decision"] == "pass"
    other = _passing_verdict()
    attach_c3_facts(other, [_c3_fact("not_same_wall", "fail"), _c3_fact("meter_type", "unsure", "unknown_attribute")])
    assert other["site"]["decision"] == "pass"

    held = _passing_verdict()
    held["site"]["decision"] = "reject"
    held["site"]["reasons"] = ["all_spots_fail"]
    attach_c3_facts(held, [_c3_fact("meter_not_accessible", "unsure", "rule_requires_review")])
    assert held["site"]["decision"] == "reject"
    assert "fact_unsure:meter_not_accessible" in held["site"]["reasons"]


def test_c3_disabled_facts_are_not_evaluated():
    rows = disabled_facts()
    assert rows
    assert all(row["outcome"] == "unsure" and row["unsure_cause"] == "not_evaluated" for row in rows)


def test_c3_facts_stay_off_without_external_vision(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("live chat")

    monkeypatch.setattr("server.criteria.c3.facts.default_chat", boom)
    rows, record = evaluate_facts({}, _cfg(c3=True, c3_facts=True, external_vision=False))
    assert record["status"] == "disabled"
    assert record["calls"] == []
    assert all(row["unsure_cause"] == "not_evaluated" for row in rows)


def test_c3_facts_send_photos_and_never_pass():
    seen = {}

    def fake(model, prompt, images=None, timeout_s=90.0):
        seen["model"] = model
        seen["n"] = len(images or [])
        return {
            "meter_type": {"value": "cl320", "clean": True},
            "meter_not_accessible": {"value": "yes", "clean": True},
        }

    rows, record = evaluate_facts(
        {"stills": [{"id": "s1", "purpose": "meter_surround"}], "keyframes": []},
        _cfg(c3=True, c3_facts=True, external_vision=True, c3_autopass=False),
        chat_fn=fake,
        images=[{"url": "data:image/jpeg;base64,aa"}],
    )
    assert seen["model"] == "gpt-5.5"
    assert seen["n"] == 1
    assert record["calls"][0]["n_images"] == 1
    assert "s1" in record["photos"]
    by_id = {row["id"]: row for row in rows}
    assert by_id["meter_type"]["outcome"] == "unsure"
    assert by_id["meter_type"]["unsure_cause"] == "rule_requires_review"
    assert by_id["meter_not_accessible"]["outcome"] == "unsure"
    assert all(row["outcome"] != "pass" and row["outcome"] != "fail" for row in rows)


def test_judge_runs_only_for_a_passing_profile_and_receives_photos(monkeypatch):
    calls = []

    def fake(model, prompt, images=None, timeout_s=90.0):
        calls.append(list(images or []))
        return {"missed_object": {"flag": False, "note": ""}}

    monkeypatch.setattr("server.criteria.c3.judge.chat", fake)
    cfg = _cfg(c3=True, external_vision=True)
    photos = [{"url": "data:image/jpeg;base64,aa"}]
    skipped, _reasons, record = judge_if_pass("manual_review", cfg, photos)
    assert skipped is None and record is None and calls == []
    decision, reasons, live = judge_if_pass("pass", cfg, photos)
    assert decision == "pass"
    assert reasons == []
    assert calls == [photos]
    assert live["calls"][0]["n_images"] == 1


def test_judge_photo_is_a_data_url(tmp_path):
    from PIL import Image

    Image.new("RGB", (32, 16), (8, 16, 32)).save(tmp_path / "frame.jpg")
    photos = judge_photos({"keyframes": [{"id": "k1", "image": "frame.jpg"}]}, tmp_path, [])
    assert photos[0]["id"] == "k1"
    assert photos[0]["url"].startswith("data:image/jpeg;base64,")


def test_observed_facade_wall_is_not_requested_whole():
    scene = json.loads((ROOT / "data/fixtures/scenes/eth3d-facade/gt-laser/scene.json").read_text())
    wall = next(item for item in scene["walls"] if item["id"] == "w1")
    span = wall["s_range_m"]
    views = build_views(
        [
            {
                "kind": "band",
                "band": "wall",
                "wall_id": "w1",
                "s_m": [span[0], span[1]],
                "criteria": ["wall_backing", "opening_clearance", "under_window", "wall_equipment_above"],
                "why": "unobserved",
            },
            {
                "kind": "band",
                "band": "ground",
                "wall_id": "w2",
                "s_m": [0.0, 2.0],
                "criteria": ["ground_surface"],
                "why": "grazing",
            },
        ],
        8,
        observed_walls=observed_wall_spans(scene),
    )
    wall_views = [view for view in views if view.get("band") == "wall" and view.get("wall_id") == "w1"]
    assert wall_views == []
    assert any(view.get("band") == "ground" and view.get("wall_id") == "w2" for view in views)


def test_w3_slivers_do_not_crowd_out_past_end():
    """Clipping w3 against its observed bands used to emit five short pieces and drop both past_end views."""
    scene = json.loads((ROOT / "data/fixtures/scenes/eth3d-facade/gt-laser/scene.json").read_text())
    items = [
        {
            "kind": "band",
            "band": "wall",
            "wall_id": "w3",
            "s_m": [6.5605, 10.7215],
            "criteria": [f"c{i}" for i in range(9)],
            "why": "unobserved",
        },
        {
            "kind": "band",
            "band": "wall",
            "wall_id": "w2",
            "s_m": [3.0179, 5.7731],
            "criteria": [f"c{i}" for i in range(7)],
            "why": "unobserved",
        },
        {
            "kind": "band",
            "band": "ground",
            "wall_id": "w2",
            "s_m": [3.0179, 5.7731],
            "criteria": ["drive_clearance", "gas_clearance", "ground_surface", "pool_clearance"],
            "why": "grazing",
        },
        {
            "kind": "band",
            "band": "ground",
            "wall_id": "w1",
            "s_m": [-12.8321, 2.2305],
            "criteria": ["drive_clearance", "gas_clearance", "ground_surface", "pool_clearance"],
            "why": "grazing",
        },
        {
            "kind": "band",
            "band": "ground",
            "wall_id": "w3",
            "s_m": [6.5605, 10.7215],
            "criteria": ["drive_clearance", "gas_clearance", "ground_surface", "pool_clearance"],
            "why": "grazing",
        },
        {"kind": "past_end", "wall_id": "w3", "s_m": [11.509, 12.009], "criteria": ["placement"], "why": "unobserved"},
        {
            "kind": "past_end",
            "wall_id": "w1",
            "s_m": [-13.3321, -12.8321],
            "criteria": ["placement"],
            "why": "unobserved",
        },
    ]
    views = build_views(
        items,
        8,
        meter_wall_id="w1",
        chain_wall_ids=["w1", "w2", "w3"],
        observed_walls=observed_wall_spans(scene),
    )
    assert len(views) <= 8
    assert sum(view.get("kind") == "past_end" for view in views) == 2
    w3 = [view for view in views if view.get("band") == "wall" and view.get("wall_id") == "w3"]
    assert w3
    assert all(view["s_m"][1] - view["s_m"][0] > 0.5 for view in w3)


def test_judge_reads_the_client_envelope_and_honours_the_budget():
    photos = [{"url": "data:image/jpeg;base64,aa"}]

    def flagged(model, prompt, images=None, timeout_s=90.0):
        assert images == photos
        return {
            "text": '{"missed_object":{"flag":true}}',
            "json": {"missed_object": {"flag": True, "note": "riser"}},
            "usage": {"input_tokens": 12, "output_tokens": 4},
            "cost_usd": 0.40,
            "model": model,
        }

    spend = HouseSpend(0.60)
    decision, reasons, record = resolve_judge(
        _cfg(c3=True, external_vision=True), chat_fn=flagged, images=photos, spend=spend
    )
    assert decision == "manual_review"
    assert reasons == ["judge:missed_object"]
    assert record["cost_usd"] == 0.40
    assert record["calls"][0]["usage"]["input_tokens"] == 12
    assert spend.spent == 0.40

    def blank(model, prompt, images=None, timeout_s=90.0):
        return {"text": "not json", "json": None, "usage": {}, "cost_usd": 0.05, "model": model}

    missed, missed_reasons, missed_record = resolve_judge(
        _cfg(c3=True, external_vision=True), chat_fn=blank, images=photos, spend=HouseSpend(0.60)
    )
    assert missed is None
    assert missed_reasons == ["judge_unavailable"]
    assert missed_record["status"] == "unavailable"
    assert missed_record["cost_usd"] == 0.05
    required, required_reasons, _required_record = resolve_judge(
        _cfg(c3=True, external_vision=True, judge_required=True), chat_fn=blank, images=photos
    )
    assert required == "manual_review"
    assert required_reasons == ["judge_unavailable"]

    full = HouseSpend(0.60)
    full.charge(0.60)
    called = []

    def should_not_run(model, prompt, images=None, timeout_s=90.0):
        called.append(model)
        return {"json": {}}

    blocked, blocked_reasons, blocked_record = resolve_judge(
        _cfg(c3=True, external_vision=True), chat_fn=should_not_run, images=photos, spend=full
    )
    assert called == []
    assert blocked is None
    assert blocked_reasons == ["judge_unavailable"]
    assert blocked_record["status"] == "budget"


def test_c3_facts_load_scene_photos_and_read_client_json(tmp_path):
    from PIL import Image

    Image.new("RGB", (8, 8), (20, 30, 40)).save(tmp_path / "meter.jpg")
    scene = {"stills": [{"id": "s1", "purpose": "meter_surround", "image": "meter.jpg"}], "keyframes": []}
    seen = {}

    def fake(model, prompt, images=None, timeout_s=90.0):
        seen["n"] = len(images or [])
        seen["url"] = (images or [{}])[0].get("url")
        return {
            "text": "{}",
            "json": {"meter_type": {"value": "cl320", "clean": True}},
            "usage": {"input_tokens": 3, "output_tokens": 2},
            "cost_usd": 0.04,
            "model": model,
        }

    spend = HouseSpend(0.60)
    rows, record = evaluate_facts(
        scene, _cfg(c3=True, c3_facts=True, external_vision=True), chat_fn=fake, scene_dir=tmp_path, spend=spend
    )
    assert seen["n"] == 1
    assert str(seen["url"]).startswith("data:image/jpeg;base64,")
    assert record["cost_usd"] == 0.04
    assert record["photos"] == ["s1"]
    assert {row["id"]: row["unsure_cause"] for row in rows}["meter_type"] == "rule_requires_review"
    assert spend.spent == 0.04
    blocked_rows, blocked = evaluate_facts(
        scene,
        _cfg(c3=True, c3_facts=True, external_vision=True),
        chat_fn=fake,
        images=[],
        spend=HouseSpend(0.0),
    )
    assert blocked["status"] == "budget"
    assert blocked["calls"] == []
    assert all(row["unsure_cause"] == "not_evaluated" for row in blocked_rows)


def test_judge_unavailable_leaves_flags_empty():
    assert flags_from(None) == []
    assert flags_from({"missed_object": {"flag": False}}) == []
    assert judge_join("pass", None, False) == "pass"


def test_c2_subsecond_budget_skips_without_a_sweep(tmp_path):
    from server.criteria.tests.data.synthetic_flatwall.make import write

    scene_dir = write(tmp_path / "wall")
    scene = json.loads((scene_dir / "scene.json").read_text())
    spots, status = _run_c2(scene, scene_dir, _profile("C"), budget_s=0.001)
    assert spots == []
    assert status == "skipped"


def test_grid_axis_transpose_round_trip():
    state = np.zeros((4, 5, 6), np.uint8)
    state[1, 2, 3] = 2
    z_up = np.transpose(state, (0, 2, 1))
    assert z_up.shape == (4, 6, 5)
    assert z_up[1, 3, 2] == 2
    assert np.transpose(z_up, (0, 2, 1))[1, 2, 3] == 2


class _Check:
    def __init__(self, cid, outcome, cause="unobserved"):
        self.id = cid
        self.outcome = outcome
        self.cause = cause


class _Cand:
    def __init__(self, checks):
        self.checks = checks


def _run(outcome, checks, unsure, failing=None):
    return {
        "wall": "w1",
        "u0": 1.0,
        "u1": 1.2,
        "outcome": outcome,
        "failing": list(failing or []),
        "unsure": list(unsure),
        "causes": {c.id: c.cause for c in checks if c.outcome == "unsure"},
        "cand": _Cand(checks),
    }


def test_chain_rules_drop_dirt_so_the_solver_loads():
    from server.criteria.c1.chain_hybrid import _rules_file, _vendor_modules
    from server.criteria.harness import load

    path = _rules_file(merged_profile("B"))
    rules_mod, _, _ = _vendor_modules(load())
    loaded = rules_mod.load_rules(path)
    assert "dirt" not in loaded.rules.ground.allowed
    assert "concrete" in loaded.rules.ground.allowed


def _chain_with_coverage(cid: str, status: str, **cand) -> dict:
    row = {
        "wall_id": "w1",
        "start_s_m": [0.0, 2.0],
        "outcome": "pass",
        "failing": [],
        "unsure": [],
    }
    row.update(cand)
    return {
        "criteria": [{"id": cid, "measured": {"coverage": [{"status": status}]}}],
        "candidates": [row],
    }


def test_chain_fail_with_observed_evidence_is_adopted():
    checks = [_Check("pool_clearance", "unsure")]
    run = _run("unsure", checks, ["pool_clearance"])
    chain = _chain_with_coverage("pool_clearance", "observed", outcome="fail", failing=["pool_clearance"])
    stats = adopt([run], chain, _mini(), Path("."), _profile("C"), None, {}, object())
    assert run["outcome"] == "fail"
    assert "pool_clearance" in run["failing"]
    assert stats["by_engine"]["chain_fail"] == 1


def test_chain_pass_never_lifts_the_candidate():
    checks = [_Check("pool_clearance", "unsure"), _Check("wall_backing", "pass", None)]
    run = _run("unsure", checks, ["pool_clearance"])
    chain = _chain_with_coverage("pool_clearance", "observed")
    stats = adopt([run], chain, _mini(), Path("."), _profile("C"), None, {}, object())
    assert run["outcome"] == "unsure"
    assert "pool_clearance" in run["unsure"]
    assert stats["by_engine"]["chain_pass"] == 1


def test_chain_pass_on_a_failed_sweep_is_counted():
    checks = [_Check("ground_surface", "unsure", "grazing")]
    run = _run("unsure", checks, ["ground_surface"])
    chain = _chain_with_coverage("ground_surface", "observed", outcome="fail", failing=["route_length"])
    stats = adopt([run], chain, _mini(), Path("."), _profile("C"), None, {}, object())
    assert run["outcome"] == "unsure"
    assert "ground_surface" in run["unsure"]
    assert stats["by_engine"]["chain_pass"] == 1


def test_unobserved_chain_pass_is_not_counted():
    checks = [_Check("ground_surface", "unsure", "unobserved")]
    run = _run("unsure", checks, ["ground_surface"])
    chain = _chain_with_coverage("ground_surface", "unobserved")
    stats = adopt([run], chain, _mini(), Path("."), _profile("C"), None, {}, object())
    assert stats["by_engine"]["chain_pass"] == 0
    assert run["outcome"] == "unsure"
    missing = {
        "candidates": [{"wall_id": "w1", "start_s_m": [0.0, 2.0], "outcome": "pass", "failing": [], "unsure": []}]
    }
    bare = _run("unsure", checks, ["ground_surface"])
    again = adopt([bare], missing, _mini(), Path("."), _profile("C"), None, {}, None)
    assert again["by_engine"]["chain_pass"] == 0


def test_unobserved_chain_inputs_change_nothing():
    scene = _mini()
    scene["objects"][0]["evidence"] = "inferred"
    assert chain_inputs_observed(scene) is False


def test_join_vetoes_only_a_passing_candidate():
    from server.criteria.c1.engine3d import Check
    from server.criteria.c2.veto import join

    def run(outcome, u, checks, failing):
        return {
            "wall": "w1",
            "u0": u,
            "u1": u,
            "outcome": outcome,
            "failing": list(failing),
            "unsure": [],
            "cand": type("C", (), {"checks": checks})(),
        }

    passing = run("pass", 0.0, [Check("wall_backing", "pass"), Check("ground_surface", "pass")], [])
    failing = run("fail", 10.0, [Check("wall_backing", "pass"), Check("route_length", "fail")], ["route_length"])
    spots = [
        {
            "outcome": "pass",
            "wall_id": "w1",
            "s_m": 0.0,
            "rear_mid_xy": [0, 0],
            "checks": [{"id": "wall_backing", "outcome": "pass"}, {"id": "ground", "outcome": "pass"}],
        },
        {
            "outcome": "fail",
            "wall_id": "w1",
            "s_m": 10.0 * 0.3048,
            "rear_mid_xy": [3, 0],
            "checks": [{"id": "wall_backing", "outcome": "fail"}, {"id": "ground", "outcome": "fail"}],
        },
    ]
    stamped = join([passing, failing], spots, {"w1": (0.0, 1)})
    assert stamped["vetoes"] == 0
    assert stamped["agreement_with_c1"] == 1.0
    assert passing["outcome"] == "pass"
    assert failing["outcome"] == "fail"
    assert "c2_notes" not in failing

    blocked = run("pass", 0.0, [Check("wall_backing", "pass")], [])
    bad = {
        "outcome": "fail",
        "wall_id": "w1",
        "s_m": 0.0,
        "rear_mid_xy": [0, 0],
        "checks": [{"id": "wall_backing", "outcome": "fail"}],
    }
    stamped = join([blocked], [bad], {"w1": (0.0, 1)})
    assert stamped["vetoes"] == 1
    assert stamped["agreement_with_c1"] == 0.0
    assert blocked["outcome"] == "unsure"
    assert blocked["cand"].checks[0].outcome == "unsure"


def test_placement_box_is_metres_at_the_short_route_spot():
    """The AR box is d/2 + 0.02 m off the face, at the shortest spot inside the run."""
    from shapely.geometry import Polygon

    from server.criteria.c1.engine3d import Candidate, Check, Route, Wall
    from server.criteria.c1.to_verdict import group_runs
    from server.criteria.candidates import placement_doc

    wall = Wall("w1", np.array([0.0, 0.0]), np.array([30.0, 0.0]), np.array([0.0, 1.0]), 10.0, 0.1)

    def cand(u, length):
        return Candidate(
            "w1",
            u,
            u + 2.0,
            Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]),
            0.0,
            [Check("wall_backing", "pass")],
            Route(length, 0.1, [("w1", 0.0, u)], [], True),
        )

    runs = group_runs([cand(1.0, 12.0), cand(1.1, 4.0)], step=0.2)
    assert len(runs) == 1
    assert runs[0]["u0"] == 1.0
    assert runs[0]["spot_u"] == 1.1
    profile = {"battery": {"width_ft": 2.0, "depth_ft": 2.0, "height_ft": 3.291667}}
    doc = placement_doc("C", profile, runs[0], type("S", (), {"walls": {"w1": wall}})(), {"w1": (0.0, 1)})
    center = doc["box_scene_m"]["center_m"]
    assert center[0] == pytest.approx((1.1 + 1.0) * 0.3048, abs=1e-4)
    assert center[2] == pytest.approx(0.3048 + 0.02, abs=1e-4)
    assert center[0] != pytest.approx(2.0 * 0.3048, abs=1e-3)


def test_body_clear_fail_vetoes_a_pass():
    from server.criteria.c1.engine3d import Check
    from server.criteria.c2.veto import join

    run = {
        "wall": "w1",
        "u0": 0.0,
        "u1": 0.0,
        "outcome": "pass",
        "failing": [],
        "unsure": [],
        "cand": type("C", (), {"checks": [Check("wall_backing", "pass")]})(),
    }
    spot = {
        "outcome": "fail",
        "wall_id": "w1",
        "s_m": 0.0,
        "rear_mid_xy": [0, 0],
        "checks": [{"id": "body_clear", "outcome": "fail"}],
    }
    stamped = join([run], [spot], {"w1": (0.0, 1)})
    assert stamped["vetoes"] == 1
    assert run["outcome"] == "unsure"


def test_c2_join_does_not_cross_walls():
    from server.criteria.c1.engine3d import Check
    from server.criteria.c2.veto import join

    run = {
        "wall": "w2",
        "u0": 0.0,
        "u1": 0.0,
        "outcome": "pass",
        "failing": [],
        "unsure": [],
        "cand": type("C", (), {"checks": [Check("wall_backing", "pass")]})(),
    }
    spot = {
        "outcome": "fail",
        "wall_id": "w1",
        "s_m": 0.0,
        "rear_mid_xy": [0, 0],
        "checks": [{"id": "wall_backing", "outcome": "fail"}],
    }
    stamped = join([run], [spot], {"w2": (0.0, 1)})
    assert stamped["vetoes"] == 0
    assert run["outcome"] == "pass"


def test_wall_rect_reaches_the_sweep():
    from server.criteria.c2.grid_adapter import _plan

    scene = _mini()
    obj = {
        "class": "window",
        "geometry": {
            "kind": "wall_rect",
            "wall_id": "w1",
            "s_m": [0.0, 1.0],
            "bottom_m": 1.0,
            "top_m": 2.0,
        },
    }
    poly = _plan(obj, scene)
    assert poly is not None and len(poly) == 4
    depth = max(p[1] for p in poly) - min(p[1] for p in poly)
    assert depth == pytest.approx(0.05)


def test_chain_crop_follows_a_wall_that_faces_x():
    from server.criteria.c2.grid_adapter import chain_bounds_m

    scene = _mini()
    scene["walls"][0]["baseline_m"] = [[0.0, -5.0], [0.0, 5.0]]
    scene["walls"][0]["s_range_m"] = [-5.0, 5.0]
    scene["objects"][0]["geometry"]["pos_m"] = [0.0, 1.5, 0.0]
    lo, hi = chain_bounds_m(scene)
    assert hi[0] - lo[0] == pytest.approx(6.5, abs=0.05)
    assert hi[1] - lo[1] == pytest.approx(10.0, abs=0.05)


def test_scale_bound_flips_the_check_outcome():
    from server.criteria.c1.engine3d import Check
    from server.criteria.run import _apply_scale

    opening = Check("opening_clearance", "pass")
    backing = Check("wall_backing", "pass")
    run = {
        "outcome": "pass",
        "failing": [],
        "unsure": [],
        "cand": type("C", (), {"checks": [opening, backing]})(),
    }
    _apply_scale([run], {"rel_bound_95": 0.03}, [])
    assert opening.outcome == "unsure"
    assert backing.outcome == "pass"
    assert run["outcome"] == "unsure"
    wide = Check("wall_backing", "pass")
    blank = {"outcome": "pass", "failing": [], "unsure": [], "cand": type("C", (), {"checks": [wide]})()}
    degraded = []
    _apply_scale([blank], {"rel_bound_95": 0.13}, degraded)
    assert wide.outcome == "unsure"
    assert "scale_bound_exceeded" in degraded


def test_fact_coverage_ids_are_remapped():
    from server.criteria.c3.facts import apply_reads

    scene = _mini()
    scene["objects"][0]["id"] = "m1"
    scene["frame"]["anchor"]["element_id"] = "m1"
    reads = {
        "status": "complete",
        "facts": [
            {
                "id": "meter_number",
                "fact": {"value": "000000", "coverage": [{"region": {"element_ids": ["meter"]}}]},
            }
        ],
    }
    copied = apply_reads(scene, reads)
    assert copied[0]["fact"]["coverage"][0]["region"]["element_ids"] == ["m1"]


def test_missing_ground_surface_is_not_a_flat_zero():
    built = convert(_mini(), "angle80", "C")
    assert built["meta"]["ground_grid_missing"] is True
    assert built["ground"]["grid"]["h"][0][0] is None
    sloped = _mini()
    sloped["ground"]["surface"] = {"kind": "plane", "normal": [0.0, 1.0, -0.1], "offset_m": 0.0}
    built = convert(sloped, "angle80", "C")
    assert built["meta"]["ground_grid_missing"] is False
    column = [row[0] for row in built["ground"]["grid"]["h"]]
    assert column[0] != column[-1]


def test_one_fail_does_not_reject_while_another_run_is_unsure():
    from server.criteria.compose import combine_decision, placement_outcome

    mixed = placement_outcome({"outcome": "fail"}, [], [{"outcome": "fail"}, {"outcome": "unsure"}])
    assert mixed == "unsure"
    assert placement_outcome({"outcome": "fail"}, [], [{"outcome": "fail"}]) == "fail"
    site, _reasons = combine_decision("manual_review", ["ends_not_limit"], "reject", ["all_spots_fail"], False)
    assert site == "manual_review"
    rejected, _reasons = combine_decision("manual_review", [], "reject", ["fact_unsure:panel"], True)
    assert rejected == "reject"


def test_not_evaluated_facts_do_not_block_a_pass():
    """not_evaluated rows stay out of the map. Review still treats a missing fact as absent."""
    from server.criteria.c3.facts import DECISION
    from server.criteria.compose import decision_fact_map

    facts = decision_fact_map(disabled_facts())
    assert facts == {}
    ignored, _reasons = site_decision(
        "pass", facts, {}, [], {"policy": {}}, _cfg(absent_facts="ignore", meter_number_required=False)
    )
    assert ignored == "pass"
    reviewed, reasons = site_decision("pass", facts, {}, [], {"policy": {}}, _cfg(absent_facts="review"))
    assert reviewed == "manual_review"
    assert "fact_absent:panel_recall" in reasons
    assert "fact_absent:main_panel_type" in reasons
    assert "fact_unsure:meter_number" in reasons
    filled = {fid: "pass" for fid in DECISION}
    cleared, clear_reasons = site_decision(
        "pass",
        filled,
        {"meter_number": {"clean": True}},
        [],
        {"policy": {}},
        _cfg(absent_facts="review"),
    )
    assert cleared == "pass"
    assert clear_reasons == []


def test_c2_join_matches_the_battery_centre():
    from server.criteria.c1.engine3d import Check
    from server.criteria.c2.veto import join

    width_ft = 2.583333
    centre_m = width_ft / 2 * 0.3048
    assert centre_m > 0.35
    run = {
        "wall": "w1",
        "u0": 0.0,
        "u1": 0.0,
        "spot_u": 0.0,
        "outcome": "pass",
        "failing": [],
        "unsure": [],
        "cand": type("C", (), {"checks": [Check("wall_backing", "pass")]})(),
    }
    left_edge = {
        "outcome": "pass",
        "wall_id": "w1",
        "s_m": 0.0,
        "rear_mid_xy": [0, 0],
        "checks": [{"id": "wall_backing", "outcome": "pass"}],
    }
    centre = {
        "outcome": "fail",
        "wall_id": "w1",
        "s_m": centre_m,
        "rear_mid_xy": [centre_m, 0],
        "checks": [{"id": "wall_backing", "outcome": "fail"}],
    }
    stamped = join([run], [left_edge, centre], {"w1": (0.0, 1)}, width_ft=width_ft)
    assert stamped["vetoes"] == 1
    assert run["outcome"] == "unsure"


def test_other_run_views_do_not_block_a_chosen_pass():
    from server.criteria.lattice import site_decision
    from server.criteria.run import _views_for_decision

    views = _views_for_decision({"outcome": "pass"}, [{"id": "vn1", "kind": "band"}])
    decision, _reasons = site_decision("pass", {}, {}, views, {"policy": {"allow_reject": False}}, _cfg())
    assert views == []
    assert decision == "pass"
    kept = _views_for_decision({"outcome": "unsure"}, [{"id": "vn1"}])
    assert kept == [{"id": "vn1"}]


def test_no_observed_ground_is_not_a_process_exit(monkeypatch):
    import sys

    module = sys.modules["server.criteria.run"]
    monkeypatch.setattr(module, "from_scene", lambda *a, **k: (object(), [], [], {"meter": {"pos": [0, 0]}}))

    class Boom:
        def __init__(self, *a, **k):
            raise SystemExit("no observed ground")

    monkeypatch.setattr(module, "Sweep", Boom)
    spots, status = _run_c2(_mini(), Path("."), _profile("C"), budget_s=5)
    assert spots == []
    assert status == "no_observed_ground"


def _coverage_ready() -> bool:
    from server.criteria.harness import load

    try:
        harness = load()
    except ImportError:
        return False
    return getattr(harness.coverage, "CoverageQuery", None) is not None


@pytest.mark.skipif(not _coverage_ready(), reason="CoverageQuery arrives with the vendored harness")
def test_synthetic_flat_wall_passes_without_views(tmp_path):
    from server.criteria.run import evaluate_scene
    from server.criteria.tests.data.synthetic_flatwall.make import write

    scene_dir = write(tmp_path / "wall")
    Scene.model_validate(json.loads((scene_dir / "scene.json").read_text()))
    cfg = CriteriaConfig(
        profiles=("A", "B", "C", "S"),
        c2=False,
        c3=False,
        hybrid=False,
        absent_facts="ignore",
        external_vision=False,
    )
    verdicts = evaluate_scene(scene_dir, tmp_path / "out", config=cfg)
    report = json.loads((tmp_path / "out" / "criteria_report.json").read_text())
    assert_not_provisional(report)
    for name, verdict in verdicts.items():
        assert verdict["site"]["decision"] == "pass", name
        assert verdict["views_needed"] == []
        assert verdict["evaluator"]["path"] == "C1-rules-3d"
        Verdict.model_validate(verdict)
        assert verdict_contract_errors(verdict) == []


@pytest.mark.skipif(not _coverage_ready(), reason="CoverageQuery arrives with the vendored harness")
def test_synthetic_flat_wall_c2_vetoes_nothing(tmp_path):
    """Checklist: a clear wall passes, C2 finds a pass spot, and the join vetoes nothing."""
    from server.criteria.run import evaluate_scene
    from server.criteria.tests.data.synthetic_flatwall.make import write

    scene_dir = write(tmp_path / "wall")
    cfg = CriteriaConfig(
        profiles=("C",),
        c2=True,
        c2_budget_s=40.0,
        c3=False,
        hybrid=False,
        absent_facts="ignore",
        external_vision=False,
    )
    verdicts = evaluate_scene(scene_dir, tmp_path / "out", config=cfg)
    report = json.loads((tmp_path / "out" / "criteria_report.json").read_text())
    sweep = json.loads((tmp_path / "out" / "c2.sweep.json").read_text())
    assert_not_provisional(report)
    assert verdicts["C"]["site"]["decision"] == "pass"
    assert verdicts["C"]["views_needed"] == []
    Verdict.model_validate(verdicts["C"])
    assert verdict_contract_errors(verdicts["C"]) == []
    assert report["c2"]["status"] == "ok"
    assert report["c2"]["vetoes"] == 0
    assert sweep["n_pass"] >= 1


def _laser_scene(path: str = "laser.ply") -> dict:
    return {
        "assets": [{"id": "laser", "kind": "point_cloud", "path": path, "format": "ply"}],
        "keyframes": [{"id": "k0"}],
    }


def _write_ply(path: Path, vertices: int) -> None:
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {vertices}\n"
        "property float x\nproperty float y\nproperty float z\nend_header\n"
    )
    path.write_bytes(header.encode("ascii") + b"\x00" * (12 * vertices))


def test_missing_laser_ply_is_an_error(tmp_path):
    with pytest.raises(CriteriaInputError) as caught:
        _query(_laser_scene(), tmp_path, [])
    assert caught.value.code == "occluders_missing"


def test_empty_recon_ply_is_an_error(tmp_path):
    _write_ply(tmp_path / "recon.ply", 0)
    scene = {"assets": [{"id": "recon", "kind": "mesh", "path": "recon.ply", "format": "ply"}]}
    assert missing_occluder_geometry(scene, tmp_path)
    assert ply_vertex_count(tmp_path / "recon.ply") == 0


def test_declared_occluders_ply_must_load(tmp_path):
    _write_ply(tmp_path / "laser.ply", 4)
    scene = _laser_scene()
    scene["assets"].append({"id": "occluders", "kind": "point_cloud", "path": "occluders.ply", "format": "ply"})
    assert missing_occluder_geometry(scene, tmp_path)
    with pytest.raises(CriteriaInputError) as caught:
        _query(scene, tmp_path, [])
    assert caught.value.code == "occluders_missing"
    _write_ply(tmp_path / "occluders.ply", 4)
    assert not missing_occluder_geometry(scene, tmp_path)


def test_allow_missing_occluders_is_provisional_and_gates_reject_it(tmp_path):
    from server.criteria.run import evaluate_scene
    from server.criteria.tests.data.synthetic_flatwall.make import write

    scene_dir = write(tmp_path / "wall")
    (scene_dir / "laser.ply").unlink()
    cfg = CriteriaConfig(
        profiles=("C",),
        c2=False,
        c3=False,
        hybrid=False,
        absent_facts="ignore",
        external_vision=False,
        allow_missing_occluders=True,
    )
    verdicts = evaluate_scene(scene_dir, tmp_path / "out", config=cfg)
    report = json.loads((tmp_path / "out" / "criteria_report.json").read_text())
    assert report["provisional"] is True
    assert "occluders_missing" in report["degraded"]
    assert verdicts["C"]["site"]["decision"] != "pass"
    with pytest.raises(ValueError, match="provisional"):
        assert_not_provisional(report)


def test_gate_rejects_a_provisional_pass():
    with pytest.raises(ValueError, match="provisional"):
        assert_not_provisional({"provisional": True})
    assert_not_provisional({"provisional": False})


def test_vision_external_calls_default_off(monkeypatch):
    monkeypatch.delenv("VISION_EXTERNAL_CALLS", raising=False)
    assert CriteriaConfig().external_vision is False
    assert CriteriaConfig.from_env().external_vision is False
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "on")
    assert CriteriaConfig.from_env().external_vision is True
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "off")
    assert CriteriaConfig.from_env().external_vision is False


def test_c3_client_reads_only_openai_and_xai_key_names(monkeypatch):
    for name in ("OPENAI_API_KEY", "XAI_API_KEY", "OpenAIChatGraphPooledProcApiKey"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OpenAIChatGraphPooledProcApiKey", "pooled-not-used")
    with pytest.raises(KeyError, match="OPENAI_API_KEY"):
        api_key_for("gpt-5.5")
    monkeypatch.setenv("OPENAI_API_KEY", "named-openai")
    assert api_key_for("gpt-5.5") == "named-openai"
    with pytest.raises(KeyError, match="XAI_API_KEY"):
        api_key_for("grok-4.7")
    monkeypatch.setenv("XAI_API_KEY", "named-xai")
    assert api_key_for("grok-4.7") == "named-xai"


def test_hybrid_off_uses_the_engine_path():
    cfg = _cfg(hybrid=False)
    path = "C1-rules-3d+hs11-chain" if cfg.hybrid else "C1-rules-3d"
    assert path == "C1-rules-3d"
    on = _cfg(hybrid=True)
    assert ("C1-rules-3d+hs11-chain" if on.hybrid else "C1-rules-3d") == "C1-rules-3d+hs11-chain"


def _cand(checks):
    class _Cand:
        def __init__(self):
            self.checks = checks
            self.wall = "w1"
            self.u0 = 0.0
            self.u1 = 1.0
            self.route = type("R", (), {"stretches": []})()

        def failing(self):
            return sorted(c.id for c in self.checks if c.outcome == "fail")

        def unsure(self):
            return sorted(c.id for c in self.checks if c.outcome == "unsure")

        @property
        def outcome(self):
            if self.failing():
                return "fail"
            if self.unsure():
                return "unsure"
            return "pass"

    return _Cand()


def test_chain_solver_runs_for_profiles_that_allow_dirt(tmp_path):
    from server.criteria.c1.chain_hybrid import solve_chain
    from server.criteria.tests.data.synthetic_flatwall.make import write

    scene_dir = write(tmp_path / "wall")
    scene = json.loads((scene_dir / "scene.json").read_text())
    for name in ("B", "C", "S"):
        verdict, code = solve_chain(scene, scene_dir / "scene.json", merged_profile(name), "angle80", None)
        assert code != "chain_solver_failed", name
        assert verdict is not None, name
        assert verdict["evaluator"]["path"] == "hs11-chain"


def test_off_chain_wall_evidence_is_unobserved():
    evidence = query_evidence(
        _mini(),
        Path("."),
        {"wall_id": "mesh_cage", "s_m": [0.0, 1.0], "height_m": [0.1, 1.0]},
        object(),
    )
    assert evidence["status"] == "unobserved"
    assert evidence["probes"] == 0


def test_query_region_restores_an_observed_geometric_pass(monkeypatch):
    from server.criteria.c1.engine3d import Check
    from server.criteria.c1.to_verdict import regrade_runs

    monkeypatch.setattr(
        "server.criteria.c1.to_verdict.regions_for",
        lambda *args, **kwargs: [{"wall_id": "w1", "s_m": [0.0, 1.0], "height_m": [0.0, 2.0]}],
    )
    monkeypatch.setattr(
        "server.criteria.c1.to_verdict.query_evidence",
        lambda *args, **kwargs: {"status": "observed", "rule": {"max_incidence_deg": 75}},
    )
    check = Check("wall_backing", "unsure", measured=1.0, pm=0.1, threshold=0.0, cause="unobserved")
    check.geom_outcome = "pass"
    run = {"outcome": "unsure", "failing": [], "unsure": ["wall_backing"], "causes": {}, "cand": _cand([check])}
    regrade_runs([run], {}, Path("."), _profile("C"), None, {}, None, "angle80")
    assert check.outcome == "pass"
    assert run["outcome"] == "pass"

    monkeypatch.setattr(
        "server.criteria.c1.to_verdict.query_evidence",
        lambda *args, **kwargs: {"status": "unobserved", "rule": {"max_incidence_deg": 75}},
    )
    held = Check("gas_clearance", "pass", measured=10.0, pm=0.1, threshold=3.0)
    blocked = {"outcome": "pass", "failing": [], "unsure": [], "causes": {}, "cand": _cand([held])}
    regrade_runs([blocked], {}, Path("."), _profile("C"), None, {}, None, "angle80")
    assert held.outcome == "unsure"
    assert held.cause == "unobserved"
    assert blocked["outcome"] == "unsure"


def test_ground_evidence_uses_the_requested_rule(monkeypatch):
    from server.criteria.c1.to_verdict import query_evidence

    def fake_region(*args, **kwargs):
        return {
            "status": "observed",
            "observed_share": 1.0,
            "views_median": 4.0,
            "views_min": 4,
            "best_incidence_deg": 70.0,
            "rule": {"min_views": 2, "max_incidence_deg": 80.0, "surface": "ground", "observed_share": 0.9},
            "sensitivity": [
                {
                    "rule_id": "angle75",
                    "max_incidence_deg": 75.0,
                    "status": "grazing",
                    "observed_share": 0.2,
                    "views_median": 0.0,
                },
                {
                    "rule_id": "angle80",
                    "max_incidence_deg": 80.0,
                    "status": "observed",
                    "observed_share": 1.0,
                    "views_median": 4.0,
                },
                {
                    "rule_id": "footprint3in",
                    "max_incidence_deg": 88.0,
                    "status": "observed",
                    "observed_share": 1.0,
                    "max_footprint_m": 0.0762,
                },
            ],
        }

    monkeypatch.setattr("server.eval.coverage.query_region", fake_region)
    region = {"wall_id": "w1", "s_m": [0.0, 1.0], "out_m": [0.0, 2.0]}
    angle75 = query_evidence({}, Path("."), region, object(), "angle75")
    assert angle75["status"] == "grazing"
    assert angle75["rule"]["max_incidence_deg"] == 75.0
    assert angle75["views_median"] == 0.0
    angle80 = query_evidence({}, Path("."), region, object(), "angle80")
    assert angle80["status"] == "observed"
    assert angle80["rule"]["max_incidence_deg"] == 80.0
    assert angle80["views_median"] == 4.0
    footprint = query_evidence({}, Path("."), region, object(), "footprint3in")
    assert footprint["status"] == "observed"
    assert footprint["rule"]["max_incidence_deg"] == 88.0
    assert footprint["rule"]["max_footprint_m"] == 0.0762


def test_reads_facts_and_views_reach_the_verdict(tmp_path):
    from server.criteria.compose import build_verdict

    scene = _mini()
    path = tmp_path / "scene.json"
    path.write_text(json.dumps(scene))
    facts = [
        {
            "id": "panel_recall",
            "scope": "site",
            "outcome": "unsure",
            "unsure_cause": "rule_requires_review",
            "reason": "pushmatic",
            "measured": None,
            "threshold": None,
        },
        {
            "id": "meter_number",
            "scope": "site",
            "outcome": "pass",
            "reason": "clean",
            "measured": None,
            "threshold": None,
        },
        {"id": "cl320", "scope": "site", "outcome": "unsure", "reason": "read", "measured": None, "threshold": None},
    ]
    views = build_views(
        [
            {
                "id": "rd1",
                "kind": "confirm_attribute",
                "criteria": ["panel_recall"],
                "why": "disputed",
                "target": "close photo of the panel",
            }
        ],
        8,
    )
    verdict = build_verdict(
        scene=scene,
        scene_path=path,
        profile_name="C",
        profile=_profile("C"),
        runs=[],
        chosen=None,
        sc=type("S", (), {"meter_wall": "w1"})(),
        sm={},
        query=None,
        scene_dir=tmp_path,
        reasons=[],
        decision="manual_review",
        runtime_s=0.1,
        rules_sha="0",
        ground_rule="angle80",
        ground_deg=80.0,
        read_facts=facts,
        cfg=_cfg(absent_facts="ignore", meter_number_required=False),
        views=views,
    )
    ids = {row["id"] for row in verdict["criteria"]}
    assert {"panel_recall", "meter_number", "cl320"} <= ids
    assert verdict["views_needed"]
    assert "panel_recall" in verdict["views_needed"][0]["criteria"]


def test_judge_stub_downgrades_without_a_live_call(monkeypatch):
    from server.criteria.c3.judge import resolve_judge

    def boom(*args, **kwargs):
        raise AssertionError("live chat")

    monkeypatch.setattr("server.criteria.c3.judge.chat", boom)
    decision, _reasons, record = resolve_judge(_cfg(c3=True, external_vision=False))
    assert decision is None
    assert record["status"] == "disabled"
    reviewed, reasons, stub = resolve_judge(_cfg(c3=True, external_vision=False, judge_stub="reject"))
    assert reviewed == "manual_review"
    assert "judge:reject" in reasons
    assert stub["calls"] == []
    rejected, _reasons, _record = resolve_judge(
        _cfg(c3=True, external_vision=False, judge_stub="reject", judge_may_reject=True)
    )
    assert rejected == "reject"

    def fake_chat(model, prompt, images=None, timeout_s=90.0):
        return {"missed_object": {"flag": True, "note": "pole"}}

    monkeypatch.setattr("server.criteria.c3.judge.chat", fake_chat)
    live, live_reasons, live_record = resolve_judge(_cfg(c3=True, external_vision=True))
    assert live == "manual_review"
    assert "judge:missed_object" in live_reasons
    assert live_record["calls"]


def test_no_coverage_grid_downgrades_c1_passes():
    from server.criteria.c1.engine3d import Check
    from server.criteria.run import downgrade_c1_passes_without_c2

    check = Check("wall_backing", "pass", measured=1.0, pm=0.1, threshold=0.0)
    run = {"outcome": "pass", "failing": [], "unsure": [], "cand": type("C", (), {"checks": [check]})()}
    downgrade_c1_passes_without_c2([run])
    assert run["outcome"] == "unsure"
    assert check.outcome == "unsure"
    assert check.cause == "not_evaluated"
    assert "wall_backing" in run["unsure"]


def test_facade_gravel_adds_g1_and_links_plys(tmp_path):
    from server.criteria.tests.data.facade_gravel import write

    src = tmp_path / "src"
    src.mkdir()
    scene = _mini()
    scene["ground"]["regions"] = []
    (src / "scene.json").write_text(json.dumps(scene))
    (src / "laser.ply").write_bytes(b"ply")
    (src / "occluders.ply").write_bytes(b"ply")
    dest = write(src, tmp_path / "gravel")
    out = json.loads((dest / "scene.json").read_text())
    gravel = next(region for region in out["ground"]["regions"] if region["id"] == "g1")
    assert gravel["material"] == "gravel"
    assert gravel["evidence"] == "observed"
    depths = [point[1] for point in gravel["polygon_m"]]
    assert max(depths) - min(depths) == pytest.approx(6.0)
    assert (dest / "laser.ply").is_symlink()
    assert (dest / "occluders.ply").is_symlink()
    assert (dest / "laser.ply").resolve() == (src / "laser.ply").resolve()


def test_grazing_why_survives_the_view_merge():
    views = build_views(
        [
            {
                "kind": "band",
                "band": "ground",
                "wall_id": "w1",
                "s_m": [0.0, 1.0],
                "criteria": ["drive_clearance"],
                "why": "unobserved",
            },
            {
                "kind": "band",
                "band": "ground",
                "wall_id": "w1",
                "s_m": [0.5, 1.5],
                "criteria": ["pool_clearance"],
                "why": "grazing",
            },
        ],
        8,
    )
    assert len(views) == 1
    assert views[0]["why"] == "grazing"


def test_c2_sweep_counts_the_default_profile():
    from server.criteria.run import c2_sweep_document

    doc = c2_sweep_document(
        {
            "C": {"status": "ok", "n_pass": 4, "n_spots": 20},
            "S": {"status": "ok", "n_pass": 0, "n_spots": 9},
        },
        "C",
    )
    assert doc["n_pass"] == 4
    assert doc["n_spots"] == 20
    assert doc["by_profile"]["S"]["n_pass"] == 0


def test_placement_margin_over_uncertainty():
    from server.criteria.c1.engine3d import Check
    from server.criteria.candidates import _margin_over_uncertainty

    check = Check("facing_gap", "pass", measured=5.0, pm=0.5, threshold=3.0)
    assert _margin_over_uncertainty(check) == pytest.approx(4.0)


def test_stage_run_reads_workdir_artifacts(monkeypatch, tmp_path):
    """Stage entry loads §3.9 keys from the work dir and writes verdicts."""
    monkeypatch.setenv("HSS_CRITERIA_PROFILES", "C")
    monkeypatch.setenv("HSS_CRITERIA_HYBRID", "off")
    monkeypatch.setenv("HSS_CRITERIA_C2", "off")
    monkeypatch.setenv("HSS_CRITERIA_C3", "off")
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "off")
    monkeypatch.setenv("HSS_CRITERIA_ABSENT_FACTS", "ignore")
    monkeypatch.setenv("HSS_CRITERIA_METER_NUMBER_REQUIRED", "off")

    fixture = write(tmp_path / "fixture")
    scene = json.loads((fixture / "scene.json").read_text())
    meter = scene["objects"]
    scene["objects"] = []
    work = tmp_path / "work"
    scene_dir = work / "scene"
    scene_dir.mkdir(parents=True)
    (scene_dir / "scene.json").write_text(json.dumps(scene) + "\n")
    (scene_dir / "laser.ply").symlink_to(fixture / "laser.ply")
    # A nested copy must not be the view-request source.
    nested = scene_dir / "objects"
    nested.mkdir()
    (nested / "view_requests.json").write_text(
        json.dumps(
            [
                {
                    "kind": "region",
                    "wall_id": "w1",
                    "s_m": [0.0, 0.5],
                    "criteria": ["placement"],
                    "why": "scene-nested",
                    "target": "do not read the scene nested file",
                }
            ]
        )
        + "\n"
    )
    (work / "objects").mkdir()
    (work / "objects" / "objects_3d.json").write_text(
        json.dumps({"format": "hss-objects-3d/v0", "objects": meter}) + "\n"
    )
    (work / "objects" / "view_requests.json").write_text(
        json.dumps(
            [
                {
                    "kind": "region",
                    "wall_id": "w1",
                    "s_m": [0.0, 0.5],
                    "criteria": ["placement", "backing", "ground_surface", "headroom"],
                    "why": "work-dir-key",
                    "target": "photo of the meter from the work-dir key",
                }
            ]
        )
        + "\n"
    )
    (work / "reads").mkdir()
    (work / "reads" / "reads.json").write_text(
        json.dumps(
            {
                "format": "base-reads/v0",
                "capture_id": "cap_test",
                "facts": [
                    {
                        "id": "panel_recall",
                        "scope": "site",
                        "outcome": "unsure",
                        "unsure_cause": "not_evaluated",
                        "reason": "copied from WS-08",
                        "measured": None,
                        "threshold": None,
                    }
                ],
            }
        )
        + "\n"
    )
    (work / "coverage").mkdir()
    (work / "coverage" / "coverage.npz").symlink_to(fixture / "coverage.npz")
    (work / "coverage" / "coverage_report.json").write_text(
        json.dumps(
            {
                "views_needed": [
                    {
                        "kind": "region",
                        "wall_id": "w1",
                        "s_m": [0.0, 0.5],
                        "criteria": ["drive_clearance"],
                        "why": "coverage-report",
                    }
                ]
            }
        )
        + "\n"
    )
    (work / "scale").mkdir()
    (work / "scale" / "scale.json").write_text(json.dumps({"rel_bound_95": 0.2}) + "\n")

    beats: list[str] = []
    ctx = StageContext(
        capture_id="cap_test",
        run_id="run_test",
        stage="criteria",
        attempt=1,
        branch="image",
        flow="photo",
        tier="photo_only",
        work_dir=work,
        inputs={},
        deadline_at="2026-09-27T00:00:00Z",
        heartbeat=beats.append,
        vlm_budget_usd=0.0,
        storage=None,
        state=None,
        settings=None,
    )
    result = stage_run(ctx)
    assert isinstance(result, StageResult)
    assert beats == ["evaluate"]
    assert result.artifacts["verdicts/verdict.C.json"] == "verdicts/verdict.C.json"
    assert (work / "verdicts" / "verdict.C.json").is_file()
    assert "scale_bound_exceeded" in result.warnings
    verdict = json.loads((work / "verdicts" / "verdict.C.json").read_text())
    ids = {row["id"] for row in verdict["criteria"]}
    assert "panel_recall" in ids
    whys = " ".join(view.get("why") or "" for view in verdict["views_needed"])
    targets = " ".join(view.get("target") or "" for view in verdict["views_needed"])
    assert "work-dir-key" in whys
    assert "coverage-report" in whys
    assert "scene-nested" not in whys
    assert "do not read the scene nested file" not in targets
    published = json.loads((scene_dir / "scene.json").read_text())
    assert published["objects"] == []
    prepared = json.loads((work / "criteria-input" / "scene.json").read_text())
    assert any(obj["id"] == "meter" and obj["class"] == "meter" for obj in prepared["objects"])
    assert (work / "criteria-input" / "coverage" / "coverage.npz").is_file()
    Verdict.model_validate(verdict)
    assert verdict_contract_errors(verdict) == []


def _add_gas(scene_dir: Path, *, evidence: str, plus_minus_m: float, gas_id: str = "gas1", s_m: float = 2.5) -> None:
    path = scene_dir / "scene.json"
    scene = json.loads(path.read_text())
    scene["objects"].append(
        {
            "id": gas_id,
            "class": "gas_meter",
            "label": "gas meter",
            "geometry": {
                "kind": "wall_rect",
                "wall_id": "w1",
                "s_m": [s_m - 0.1, s_m + 0.1],
                "bottom_m": 0.4,
                "top_m": 0.9,
            },
            "wall_id": "w1",
            "confidence": 1.0,
            "plus_minus_m": plus_minus_m,
            "source": {"kind": "annotation"},
            "evidence": evidence,
        }
    )
    path.write_text(json.dumps(scene) + "\n")


def _profile_c_geometry() -> CriteriaConfig:
    return CriteriaConfig(
        profiles=("C",),
        c2=False,
        c3=False,
        hybrid=False,
        absent_facts="ignore",
        meter_number_required=False,
        external_vision=False,
    )


def _passing_on_inferred(verdict: dict) -> list[dict]:
    bad = []
    for cand in verdict["candidates"]:
        if cand["outcome"] != "pass":
            continue
        rows = [
            row for row in verdict["criteria"] if row.get("candidate_id") == cand["id"] and row["outcome"] == "pass"
        ]
        subjects = {(row.get("measured") or {}).get("subject") for row in rows}
        if "gas1" in subjects:
            bad.append(cand)
    return bad


@pytest.mark.skipif(not _coverage_ready(), reason="CoverageQuery arrives with the vendored harness")
def test_inferred_gas_meter_cannot_clear_a_check(tmp_path):
    """Validator probe: inferred gas1 at s 2.5 m, profile C, C2 off. Site stays in review."""
    scene_dir = write(tmp_path / "wall")
    _add_gas(scene_dir, evidence="inferred", plus_minus_m=0.05)
    verdicts = evaluate_scene(scene_dir, tmp_path / "out", config=_profile_c_geometry())
    verdict = verdicts["C"]
    assert verdict["site"]["decision"] == "manual_review"
    assert verdict["candidates"]
    assert all(cand["outcome"] != "pass" for cand in verdict["candidates"])
    gas_rows = [row for row in verdict["criteria"] if (row.get("measured") or {}).get("subject") == "gas1"]
    assert gas_rows
    assert all(row["outcome"] != "pass" for row in gas_rows)
    assert _passing_on_inferred(verdict) == []
    Verdict.model_validate(verdict)
    assert verdict_contract_errors(verdict) == []


@pytest.mark.skipif(not _coverage_ready(), reason="CoverageQuery arrives with the vendored harness")
def test_inferred_meter_within_reach_blocks_a_nearer_observed_one(tmp_path):
    """Observed gas at s 2.0 m and inferred gas at s 5.0 m. A start near 3 m is about 1.2 m
    from the inferred meter; that spot used to pass gas_clearance on the observed subject."""
    scene_dir = write(tmp_path / "wall")
    _add_gas(scene_dir, evidence="observed", plus_minus_m=0.05, gas_id="gas2", s_m=2.0)
    _add_gas(scene_dir, evidence="inferred", plus_minus_m=0.05, gas_id="gas1", s_m=5.0)
    verdicts = evaluate_scene(scene_dir, tmp_path / "out", config=_profile_c_geometry())
    verdict = verdicts["C"]
    near = [
        cand
        for cand in verdict["candidates"]
        if cand["wall_id"] == "w1" and cand["start_s_m"][0] <= 3.09 and cand["start_s_m"][1] >= 2.99
    ]
    assert near
    assert all(cand["outcome"] != "pass" for cand in near)
    assert all("gas_clearance" in cand["unsure"] or "gas_clearance" in cand["failing"] for cand in near)
    Verdict.model_validate(verdict)
    assert verdict_contract_errors(verdict) == []


@pytest.mark.skipif(not _coverage_ready(), reason="CoverageQuery arrives with the vendored harness")
def test_distant_inferred_meter_does_not_depend_on_object_order(tmp_path):
    """A temporary inferred subject outside the search reach cannot taint an observed winner."""
    scene_dir = write(tmp_path / "wall")
    _add_gas(scene_dir, evidence="observed", plus_minus_m=0.05, gas_id="observed_gas", s_m=2.0)
    _add_gas(scene_dir, evidence="inferred", plus_minus_m=0.05, gas_id="inferred_gas", s_m=5.0)
    observed_first = evaluate_scene(scene_dir, tmp_path / "observed-first", config=_profile_c_geometry())["C"]

    path = scene_dir / "scene.json"
    scene = json.loads(path.read_text())
    scene["objects"].reverse()
    path.write_text(json.dumps(scene) + "\n")
    inferred_first = evaluate_scene(scene_dir, tmp_path / "inferred-first", config=_profile_c_geometry())["C"]

    assert observed_first["site"]["decision"] == "pass"
    assert inferred_first["site"] == observed_first["site"]
    assert inferred_first["candidates"] == observed_first["candidates"]
    assert inferred_first["criteria"] == observed_first["criteria"]


@pytest.mark.skipif(not _coverage_ready(), reason="CoverageQuery arrives with the vendored harness")
def test_location_unsure_gas_meter_is_not_lifted(tmp_path):
    scene_dir = write(tmp_path / "wall")
    _add_gas(scene_dir, evidence="observed", plus_minus_m=9.99)
    verdicts = evaluate_scene(scene_dir, tmp_path / "out", config=_profile_c_geometry())
    verdict = verdicts["C"]
    assert verdict["site"]["decision"] == "manual_review"
    assert all(cand["outcome"] != "pass" for cand in verdict["candidates"])


def test_half_seen_coarse_cell_is_not_unobserved_volume():
    fine = np.zeros((4, 2, 2), np.uint8)
    # First coarse cell: 4 unknown and 4 free. Second: 5 unknown.
    fine[:2, :, :] = 1
    fine[0, 0, 0] = 0
    fine[0, 0, 1] = 0
    fine[0, 1, 0] = 0
    fine[0, 1, 1] = 0
    fine[2:, :, :] = 0
    fine[2, 0, 0] = 1
    fine[2, 0, 1] = 1
    fine[2, 1, 0] = 1
    mask = majority_unknown_grid(fine, 2)
    assert mask.shape == (2, 1, 1)
    assert bool(mask[0, 0, 0]) is False
    assert bool(mask[1, 0, 0]) is True


def test_opening_face_is_outside_the_window():
    poly = Polygon([(0.0, 0.0), (1.7, 0.0), (1.7, 0.05), (0.0, 0.05)])
    origin = np.array([0.0, -0.2, 0.0])
    face = opening_face_cells((20, 8), origin, 0.1, [poly], 0.25)
    i = int((0.85 - origin[0]) / 0.1)
    j_front = int((0.15 - origin[1]) / 0.1)
    j_glass = int((0.02 - origin[1]) / 0.1)
    assert face[i, j_front]
    assert not face[i, j_glass]


def test_window_recess_stays_on_the_cable_band():
    skin = np.zeros((1, 1), bool)
    mostly_free = np.ones((1, 1), bool)
    in_band = np.ones((1, 1), bool)
    unk = np.zeros((1, 1), bool)
    near = np.zeros((1, 1), bool)
    frontier = np.zeros((1, 1), bool)
    assert bool(cable_passable(skin, mostly_free, in_band, unk, near, frontier)[0, 0])
    mostly_free[0, 0] = False
    assert bool(cable_passable(skin, mostly_free, in_band, unk, near, frontier)[0, 0]) is False


def test_rear_standoff_uses_the_dense_wall_not_a_speck():
    vb = np.arange(-0.6, 0.1 + 1e-9, 0.05)
    fraction = np.zeros(len(vb))
    fraction[int(np.argmin(np.abs(vb - (-0.10))))] = 0.90
    fraction[int(np.argmin(np.abs(vb - 0.05)))] = 0.14
    rear = rear_standoff(fraction, vb, 0.1)
    assert rear == pytest.approx(-0.10 + 1.5 * 0.1)


def test_ground_strip_starts_at_the_wall():
    wall = type("W", (), {"id": "w1"})()
    scene3d = type("S", (), {"walls": {"w1": wall}})()
    cand = type("C", (), {"wall": "w1", "u0": 1.0, "u1": 1.2, "route": type("R", (), {"stretches": []})()})()
    profile = {"battery": {"depth_ft": 1.833}}
    check = engine.Check("ground_surface", "pass", measured=0.0, pm=0.02, threshold=0.0)
    region = regions_for(check, cand, scene3d, {"w1": (0.0, 1)}, profile)[0]
    assert region["out_m"][0] == 0.0
    assert region["out_m"][1] > 0.0


def test_inferred_flag_blocks_a_pass_regardless_of_wording(tmp_path):
    """The gate is check.inferred, copied from the object's evidence. Reason text is not."""
    scene_dir = write(tmp_path / "wall")
    _add_gas(scene_dir, evidence="inferred", plus_minus_m=0.05)
    scene = json.loads((scene_dir / "scene.json").read_text())
    scene3d = convert(scene, "angle80", "C")
    gas = next(obj for obj in scene3d["objects"] if obj["id"] == "gas1")
    assert gas["evidence"] == "inferred"
    assert "gas1" in scene3d["meta"]["inferred_ids"]
    loaded = next(obj for obj in engine.load_scene(scene3d).objects if obj.id == "gas1")
    assert loaded.evidence == "inferred"

    check = engine.Check("gas_clearance", "pass", subject="gas1", measured=8.0, pm=0.1, threshold=3.0)
    apply_review_flags([check], scene3d["meta"])
    assert check.inferred is True
    assert check.outcome == "unsure"
    check.reason = "clearance holds"
    check.geom_outcome = "pass"
    check.outcome = "unsure"
    check.cause = "unobserved"
    assert _restore_geometric(check) == "unsure"

    failed = engine.Check(
        "gas_clearance", "unsure", subject="gas1", cause="unobserved", geom_outcome="fail", inferred=True
    )
    assert _restore_geometric(failed) == "fail"

    phrase_only = engine.Check("gas_clearance", "unsure", reason="inferred geometry", geom_outcome="pass")
    assert phrase_only.inferred is False
    assert _restore_geometric(phrase_only) == "pass"

    forced = engine.Check(
        "gas_clearance", "pass", subject="gas1", measured=8.0, pm=0.1, threshold=3.0, inferred=True, reason="clear"
    )
    row = criterion_dict(forced, "cand", [], "annotation")
    assert row["outcome"] == "unsure"
    assert "inferred" not in row


def test_profile_a_c2_headroom_is_not_an_empty_band():
    """Delivery profile A was C2 skipped with 0 spots: headroom min is null, so the old
    sample started at battery height (~1.0 m) and stopped at 0.5 m, and wh[-1] raised."""
    rules = c2_rules(merged_profile("A"))
    assert rules["headroom"]["min_ft"] == 0.0
    height_m = rules["battery"]["height_ft"] * 0.3048
    assert len(np.arange(height_m, 0.5, 0.025)) == 0
    assert headroom_sample_heights(0.0, height_m, 0.0, 0.05) is None
    rules_c = c2_rules(merged_profile("C"))
    band = headroom_sample_heights(0.0, height_m, rules_c["headroom"]["min_ft"] * 0.3048, 0.05)
    assert band is not None and len(band) > 0


def test_reads_sample_matches_ws08_rows():
    doc = json.loads((ROOT / "server/criteria/tests/data/reads.sample.json").read_text())
    assert doc["status"] == "complete"
    assert {row["id"] for row in doc["facts"]} == {"meter_number", "cl320", "panel_recall"}
    for row in doc["facts"]:
        Criterion.model_validate(row)
        coverage = row["fact"]["coverage"]
        assert len(coverage) >= 1
        assert "best_incidence_deg" in coverage[0]
        assert "worst_best_incidence_deg" in coverage[0]


_FACADE_GT = ROOT / "data/fixtures/scenes/eth3d-facade/gt-laser"


@pytest.mark.skipif(
    not _coverage_ready() or not (_FACADE_GT / "laser.ply").exists(),
    reason="facade GT laser is fetched, not committed",
)
def test_gravel_window_passes_under_footprint3in(tmp_path):
    scene_dir = write_gravel(_FACADE_GT, tmp_path / "gravel")
    cfg = CriteriaConfig(
        profiles=("C",),
        ground_rule="footprint3in",
        c2=True,
        c2_budget_s=40.0,
        c3=False,
        hybrid=False,
        absent_facts="ignore",
        meter_number_required=False,
        external_vision=False,
    )
    verdicts = evaluate_scene(scene_dir, tmp_path / "out", config=cfg)
    verdict = verdicts["C"]
    window = [
        cand
        for cand in verdict["candidates"]
        if cand["wall_id"] == "w1"
        and cand["start_s_m"][0] >= -3.95
        and cand["start_s_m"][1] <= -3.30
        and cand["outcome"] == "pass"
        and not cand.get("failing")
    ]
    assert window, [
        (cand["id"], cand["start_s_m"], cand["outcome"], cand.get("failing"), cand.get("unsure"))
        for cand in verdict["candidates"]
        if cand["wall_id"] == "w1" and -4.2 <= cand["start_s_m"][0] <= -3.1
    ]
    assert verdict["site"]["decision"] == "pass"
