"""The C3 failure (a pass on w2, which the photos only show edge-on) must be caught.

Needs the facade GT plus every file CoverageQuery.from_scene reads for it (the scene, its
point cloud, and the occluder PLY). Skipped when any of those files is absent.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest

from server.eval.coverage import CoverageQuery, query_region
from server.eval.sceneio import load_json, schema_errors, verdict_contract_errors
from server.eval.verdictcheck import audit_claims

_REPO = Path(__file__).resolve().parents[3]
GT = Path(os.environ.get("EVALHARNESS_GT", _REPO / "data/fixtures/scenes/eth3d-facade/gt-laser/scene.json"))

W2_FACE = {"wall_id": "w2", "s_m": [3.3, 4.2], "height_m": [0.0, 1.0]}
W1_GAP = {"wall_id": "w1", "s_m": [-0.7, 0.7], "height_m": [0.0, 1.0]}


def _files_coverage_query_reads(scene_path: Path) -> list[Path]:
    """Paths CoverageQuery.from_scene opens: the scene, its point cloud (or mesh), and occluders."""
    scene = load_json(scene_path)
    base = scene_path.parent
    files = [scene_path]
    assets = scene.get("assets", [])
    cloud = next((asset for asset in assets if asset.get("kind") == "point_cloud"), None)
    if cloud is None:
        cloud = next((asset for asset in assets if asset.get("kind") == "mesh"), None)
    if cloud is not None:
        files.append(base / cloud["path"])
    files.extend(base / asset["path"] for asset in assets if asset.get("id") == "occluders")
    return files


@pytest.fixture(scope="module")
def gt():
    if not GT.is_file():
        pytest.skip(f"{GT.name} absent")
    missing = [path.name for path in _files_coverage_query_reads(GT) if not path.is_file()]
    if missing:
        pytest.skip("missing " + ", ".join(missing))
    s = load_json(GT)
    return s, CoverageQuery.from_scene(s, GT.parent)


def verdict(outcome, coverage, cause=None):
    c = {"id": "facing_gap", "scope": "candidate", "candidate_id": "c1", "outcome": outcome, "reason": "test",
         "measured": {"value_ft": 11.4, "plus_minus_ft": 0.2, "coverage": coverage},
         "threshold": {"value_ft": 5.0, "comparison": "at_least"}, "margin_ft": 6.4, "margin_over_uncertainty": 32.0}
    if cause:
        c["unsure_cause"] = cause
    return {"format": "base-verdict/v0", "verdict_id": "t", "scene": {"scene_id": "eth3d-facade/gt-laser", "site": "eth3d-facade"},
            "evaluator": {"path": "test"}, "site": {"decision": "manual_review", "reasons": []}, "criteria": [c], "views_needed": []}


def test_w2_is_grazing_and_w1_observed(gt):
    s, q = gt
    w2 = query_region(s, None, W2_FACE, q)
    w1 = query_region(s, None, W1_GAP, q)
    assert w2["status"] == "grazing" and w2["best_incidence_deg"] > 75
    assert w1["status"] == "observed" and w1["views_median"] >= 2 and w1["best_incidence_deg"] < 45


def test_pass_on_grazing_evidence_is_rejected(gt):
    s, q = gt
    v = verdict("pass", [query_region(s, None, W2_FACE, q)])
    assert not schema_errors(v)
    errs = verdict_contract_errors(v)
    assert errs and "grazing" in errs[0]
    assert not verdict_contract_errors(verdict("unsure", [query_region(s, None, W2_FACE, q)], cause="grazing"))
    assert not verdict_contract_errors(verdict("pass", [query_region(s, None, W1_GAP, q)]))


def test_bare_measurement_is_rejected_by_schema():
    v = verdict("pass", [])
    del v["criteria"][0]["measured"]["coverage"]
    assert schema_errors(v)


def test_audit_flags_a_backend_that_claims_observed(gt):
    s, q = gt
    ev = query_region(s, None, W2_FACE, q)
    lie = copy.deepcopy(ev)
    lie.update(status="observed", observed_share=1.0, views_median=6, best_incidence_deg=10.0)
    a = audit_claims(verdict("pass", [lie]), GT)
    assert a["false_observed_claims"] == 1 and a["decisive_on_false_claims"] == 1
    assert a["rows"][0]["truth"] == "grazing"
