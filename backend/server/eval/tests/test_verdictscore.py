"""Verdict scorer semantics on a synthetic verdict (no data needed)."""

from __future__ import annotations

import json
from pathlib import Path

from server.eval.verdictscore import STEP_M, classify, score

LABELS = json.loads((Path(__file__).resolve().parents[1] / "labels/eth3d-facade.demo.labels.json").read_text())
GRAZING = {"region": {"wall_id": "w2", "s_m": [3.3, 4.2], "height_m": [0, 1]}, "status": "grazing", "observed_share": 0.0,
           "views_min": 0, "views_median": 0.0, "best_incidence_deg": 78.2, "rule": {"min_views": 2, "surface": "wall", "max_incidence_deg": 75.0}}


def verdict(decision, cands, crit=()):
    return {"format": "base-verdict/v0", "verdict_id": "t", "scene": {"scene_id": "x", "site": "eth3d-facade"}, "evaluator": {"path": "test"},
            "site": {"decision": decision, "reasons": []}, "criteria": list(crit), "candidates": cands, "views_needed": []}


def test_classify_table():
    assert classify("not_pass", "pass") == "unsafe_pass"
    assert classify("not_pass", "unsure") == "agree"
    assert classify("borderline", "fail") == "missed_review"
    assert classify("pass", "fail") == "false_rejection"
    assert classify("fail", "unsure") == "over_caution"
    assert classify("unknown", "pass") == "pass_on_unknown"


def test_correct_path_scores_clean():
    v = verdict("manual_review", [{"id": "a", "wall_id": "w1", "start_s_m": [-12.9, 2.3], "outcome": "fail"},
                                  {"id": "b", "wall_id": "w2", "start_s_m": [3.0, 5.8], "outcome": "unsure"},
                                  {"id": "c", "wall_id": "w3", "start_s_m": [6.5, 10.8], "outcome": "fail"}])
    r = score(v, LABELS)
    assert r["headline"]["unsafe_passes"] == 0 and r["start_range"]["agreement"] == 1.0
    assert r["site"]["decision"]["result"] == "agree"


def test_unsafe_passes_are_counted():
    crit = [{"id": "facing_gap", "scope": "candidate", "candidate_id": "b", "outcome": "pass", "reason": "t",
             "measured": {"value_ft": 11.4, "plus_minus_ft": 0.2, "coverage": [GRAZING]}, "threshold": None}]
    v = verdict("pass", [{"id": "a", "wall_id": "w1", "start_s_m": [-5.0, -3.0], "outcome": "pass"},
                         {"id": "b", "wall_id": "w2", "start_s_m": [3.2, 4.0], "outcome": "pass"}], crit)
    r = score(v, LABELS)
    h = r["headline"]
    # w1 [-5, -3] and w2 [3.2, 4.0] (w2 is not_pass since the arched window was added)
    assert abs(h["unsafe_pass_wall_m"] - 2.8) < 3 * STEP_M
    assert h["unsafe_site_decision"] and h["coverage_contract_unsafe_passes"] == 1
