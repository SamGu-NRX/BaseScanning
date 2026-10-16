"""Gate rows fail closed. Missing engines SKIP; a bad verdict is a FAIL and exit 1."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml

from server.eval.cli import main
from server.eval.e2e import main as e2e_main
from server.eval.gates import (
    SPAN_TOL_M,
    _apply_known_failure,
    _cmp,
    _decidability_rows,
    _false_rejections,
    _geometry_rows,
    _known_warning_row,
    _overlaps,
    _recomputed_unsafe,
    _rescored_rows,
    _row,
    _stripped_coverage_pass,
    evaluate,
    exit_code,
    refresh_committed_hashes,
)

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "server" / "eval" / "gates.yaml"
FIXTURES = ROOT / "data" / "fixtures"


def _config_for(tmp_path: Path, files: dict[str, Path]) -> Path:
    """Copy gates.yaml and retarget pins so a planted file is the committed bytes."""
    text = CONFIG.read_text()
    for rel, path in files.items():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        prefix = f"  {rel}: "
        lines = []
        replaced = False
        for line in text.splitlines(keepends=True):
            if line.startswith(prefix):
                lines.append(f"{prefix}{digest}\n")
                replaced = True
            else:
                lines.append(line)
        if not replaced:
            raise AssertionError(f"no sha256 pin for {rel}")
        text = "".join(lines)
    dest = tmp_path / "gates.yaml"
    dest.write_text(text)
    return dest


def _verdict(decision: str, *, unsure_cause: str | None = None) -> dict:
    site = {"decision": decision, "reasons": []}
    if unsure_cause:
        site["unsure_cause"] = unsure_cause
    return {
        "site": site,
        "candidates": [
            {"id": "a", "wall_id": "w1", "start_s_m": [-3.8913691473399123, -3.8913691473399123], "outcome": "pass"},
            {"id": "b", "wall_id": "w1", "start_s_m": [-3.383267547339912, -3.383267547339912], "outcome": "pass"},
        ],
        "criteria": [{"id": "route_length", "outcome": "pass", "measured": {"value_ft": 8.5166}}],
    }


def test_rounded_window_contains_the_rederived_endpoints():
    lo, hi = -3.8913691473399123, -3.383267547339912
    assert hi >= -3.891 - SPAN_TOL_M
    assert lo <= -3.383 + SPAN_TOL_M
    assert lo < -3.891


def test_committed_fixtures_are_scored_against_their_labels():
    report = evaluate(CONFIG, ROOT / "data" / "fixtures", repo=ROOT)
    by_id = {row["id"]: row for row in report.rows}
    demo = by_id["verdict/eth3d-facade/demo/start_agreement"]
    assert demo["status"] == "PASS"
    assert demo["value"] == 1.0
    assert by_id["verdict/eth3d-facade/demo/unsafe_passes"]["status"] == "PASS"
    assert by_id["verdict/eth3d-facade/A/false_rejections"]["status"] == "PASS"
    assert by_id["verdict/eth3d-facade/A/pass_recall"]["status"] == "SKIP"
    assert by_id["verdict/eth3d-facade/A/site_agreement"]["status"] == "PASS"
    assert by_id["verdict/eth3d-delivery/S/unsafe_passes"]["value"] == 0
    assert report.summary["fail"] == 0


def test_offline_table_skips_the_engine_and_passes_the_unpatched_reference():
    report = evaluate(CONFIG, ROOT / "data" / "fixtures", repo=ROOT)
    by_id = {row["id"]: row for row in report.rows}
    unpatched = by_id["positive/eth3d-facade/gt-laser/C/footprint3in"]
    assert unpatched["status"] == "PASS"
    assert unpatched["value"] == "manual_review"
    gravel = by_id["positive/eth3d-facade/gt-laser-gravel/C/footprint3in"]
    assert gravel["status"] == "KNOWN_FAIL"
    assert gravel["value"] == "pass"
    assert "route 9.1834 ft exceeds 9.0" in gravel["note"]
    assert "conservative miss from near-wall self-occlusion, SD-96" in gravel["note"]
    assert "owner WS-09" in gravel["note"]
    assert "until 2026-10-04" in gravel["note"]
    assert "not_merged" not in gravel["note"]
    assert by_id["positive/eth3d-facade/gt-laser-gravel/C/angle80"]["status"] == "PASS"
    assert by_id["positive/eth3d-facade/gt-laser-gravel/C/angle80"]["value"] == "manual_review"
    angle = json.loads((ROOT / "data/fixtures/positive/eth3d-facade/gt-laser-gravel/C/angle80.json").read_text())
    assert not any(str(reason).startswith("fact_absent") for reason in (angle["site"].get("reasons") or []))
    assert by_id["decidability/eth3d-facade/gt-laser/C"]["status"] == "PASS"
    assert by_id["decidability/eth3d-electro/gt-laser/C"]["status"] == "PASS"
    assert by_id["safety/no_coverage/facade-C-c2"]["status"] == "PASS"
    assert by_id["safety/no_coverage/facade-C-noc2"]["status"] == "PASS"
    assert by_id["safety/veto/facade-C-0000"]["status"] == "PASS"
    assert by_id["safety/judge_monotone"]["status"] == "SKIP"
    assert by_id["lamar/2022-02-10_s0/openings_p90_in"]["status"] == "PASS"
    assert by_id["lamar/2022-02-10_s0/openings_p90_in"]["value"] == 3.75
    assert by_id["lamar/host"]["status"] == "PASS"
    assert by_id["lamar/host"]["value"] == "ws-11-ci"
    assert by_id["lamar/host"]["threshold"] == "ws-11-ci"
    assert str(by_id["lamar/host"]["evidence_path"]).endswith("data/fixtures/lamar/2022-02-10_s0.json")
    assert by_id["lamar/openings_p90_in"]["status"] == "SKIP"
    warn = by_id["geometry/eth3d-facade/metric/ground_strip"]
    assert warn["status"] == "WARN"
    assert warn["value"] == 0.069
    assert warn["threshold"] == 0.069
    assert "false_observed_by_rule 0.069" in warn["note"]
    assert "floor 0.0247" in warn["note"]
    assert "claims empty" in warn["note"]
    assert str(warn["evidence_path"]).endswith("geometry/eth3d-facade/metric/ground_strip.json")
    assert report.summary["fail"] == 0
    assert report.summary["known_fail"] == 1
    assert exit_code(report) == 0
    loaded = yaml.safe_load(CONFIG.read_text())
    assert "yt-12Ks1idWj40" not in loaded["e2e_packets"]
    assert "yt-12Ks1idWj40" in loaded["placement_excluded_sites"]


def test_unsafe_pass_exits_1(tmp_path: Path):
    inputs = tmp_path / "inputs"
    target = inputs / "verdicts" / "eth3d-facade" / "c1-3d"
    target.mkdir(parents=True)
    planted = target / "C.json"
    planted.write_text(
        json.dumps(
            {
                "site": {"decision": "pass"},
                "candidates": [{"id": "spot", "wall_id": "w1", "outcome": "pass"}],
            }
        )
    )
    config = _config_for(tmp_path, {"verdicts/eth3d-facade/c1-3d/C.json": planted})
    out = tmp_path / "gates.json"
    code = main(["gates", "--config", str(config), "--inputs", str(inputs), "--out", str(out)])
    assert code == 1
    report = json.loads(out.read_text())
    failed = [row for row in report["rows"] if row["status"] == "FAIL"]
    assert any(row["metric"] == "unsafe_passes" and row["value"] == 1 for row in failed)


def test_stripped_coverage_that_passes_is_a_fail(tmp_path: Path):
    folder = tmp_path / "safety" / "no_coverage"
    folder.mkdir(parents=True)
    (folder / "C.json").write_text(json.dumps({"decision": "pass", "pass_candidates": 2}))
    report = evaluate(CONFIG, tmp_path, repo=ROOT)
    row = next(r for r in report.rows if r["id"] == "safety/no_coverage/C")
    assert row["status"] == "FAIL"
    assert exit_code(report) == 1


def test_positive_pass_flipped_to_manual_review_fails(tmp_path: Path):
    dest = tmp_path / "positive" / "eth3d-facade" / "gt-laser-gravel" / "C"
    dest.mkdir(parents=True)
    planted = dest / "footprint3in.json"
    planted.write_text(json.dumps(_verdict("manual_review")))
    config = _config_for(tmp_path, {"positive/eth3d-facade/gt-laser-gravel/C/footprint3in.json": planted})
    report = evaluate(config, tmp_path, repo=ROOT)
    row = next(r for r in report.rows if r["id"] == "positive/eth3d-facade/gt-laser-gravel/C/footprint3in")
    assert row["status"] == "FAIL"
    assert row["value"] == "manual_review"
    assert exit_code(report) == 1


def test_route_over_the_bar_is_known_until_expiry(tmp_path: Path):
    inputs = tmp_path / "inputs"
    shutil.copytree(FIXTURES, inputs)
    verdict = _verdict("pass")
    verdict["criteria"][0]["measured"]["value_ft"] = 9.1834
    planted = inputs / "positive/eth3d-facade/gt-laser-gravel/C/footprint3in.json"
    planted.write_text(json.dumps(verdict))
    config = _config_for(tmp_path, {"positive/eth3d-facade/gt-laser-gravel/C/footprint3in.json": planted})
    report = evaluate(config, inputs, repo=ROOT)
    row = next(r for r in report.rows if r["id"] == "positive/eth3d-facade/gt-laser-gravel/C/footprint3in")
    assert row["status"] == "KNOWN_FAIL"
    assert exit_code(report) == 0
    expired = _apply_known_failure(
        _row("id", "positive_pass", "t", "site.decision", "pass", "pass", "FAIL", None, "route 9.1834 ft exceeds 9.0"),
        {
            "known_failure": {
                "reason": "striped",
                "owner": "WS-09",
                "until": "2026-10-04",
                "route_ft": 9.1834,
                "tolerance_ft": 0.05,
            }
        },
        today=date(2026, 10, 5),
    )
    assert expired["status"] == "FAIL"


def test_route_above_the_recorded_miss_is_a_real_fail(tmp_path: Path):
    dest = tmp_path / "positive" / "eth3d-facade" / "gt-laser-gravel" / "C"
    dest.mkdir(parents=True)
    verdict = _verdict("pass")
    verdict["criteria"][0]["measured"]["value_ft"] = 14.0
    planted = dest / "footprint3in.json"
    planted.write_text(json.dumps(verdict))
    config = _config_for(tmp_path, {"positive/eth3d-facade/gt-laser-gravel/C/footprint3in.json": planted})
    report = evaluate(config, tmp_path, repo=ROOT)
    row = next(r for r in report.rows if r["id"] == "positive/eth3d-facade/gt-laser-gravel/C/footprint3in")
    assert row["status"] == "FAIL"
    assert "route 14.0 ft exceeds 9.0" in row["note"]
    assert "known failure" not in row["note"]
    assert report.summary["known_fail"] == 0
    assert exit_code(report) == 1


_NON_FINITE = [True, "8.5", float("nan"), False, "abc"]


@pytest.mark.parametrize("value_ft", _NON_FINITE, ids=["true", "numeric-string", "nan", "false", "abc"])
def test_gravel_route_rejects_non_finite_values(tmp_path: Path, value_ft: object):
    dest = tmp_path / "positive" / "eth3d-facade" / "gt-laser-gravel" / "C"
    dest.mkdir(parents=True)
    verdict = _verdict("pass")
    verdict["criteria"][0]["measured"]["value_ft"] = value_ft
    planted = dest / "footprint3in.json"
    planted.write_text(json.dumps(verdict))
    config = _config_for(tmp_path, {"positive/eth3d-facade/gt-laser-gravel/C/footprint3in.json": planted})
    report = evaluate(config, tmp_path, repo=ROOT)
    row = next(item for item in report.rows if item["id"] == "positive/eth3d-facade/gt-laser-gravel/C/footprint3in")
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"
    assert exit_code(report) == 1


@pytest.mark.parametrize("value", _NON_FINITE, ids=["true", "numeric-string", "nan", "false", "abc"])
def test_comparisons_reject_non_finite_values(value: object):
    row = _cmp("id", "group", "target", "metric", value, 100, Path("evidence.json"), at_most=True)
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"


@pytest.mark.parametrize("value", _NON_FINITE, ids=["true", "numeric-string", "nan", "false", "abc"])
def test_geometry_scale_rejects_non_finite_values(tmp_path: Path, value: object):
    evidence = tmp_path / "geometry" / "site" / "variant" / "geomscore.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(json.dumps({"scale": {"scale_error_pct": value}}))
    rows = _geometry_rows({"geometry": {"site/variant": {"scale_error_pct_abs_max": 100}}}, tmp_path)
    row = next(item for item in rows if item["metric"] == "abs(scale_error_pct)")
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"


@pytest.mark.parametrize("value", _NON_FINITE, ids=["true", "numeric-string", "nan", "false", "abc"])
def test_known_warning_rejects_non_finite_values(tmp_path: Path, value: object):
    _plant_ground_strip(tmp_path, _ground_strip(value, []))
    row = _known_warning_row(
        {
            "id": "geometry/eth3d-facade/metric/ground_strip",
            "metric": "false_observed_by_rule",
            "evidence": "geometry/eth3d-facade/metric/ground_strip.json",
            "value_key": "false_observed_by_rule",
            "recorded": 0.069,
            "tolerance": 0.001,
        },
        tmp_path,
        pins=None,
    )
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"


@pytest.mark.parametrize("value", _NON_FINITE, ids=["true", "numeric-string", "nan", "false", "abc"])
def test_pass_candidates_reject_non_finite_values(value: object):
    reason = _stripped_coverage_pass({"site": {"decision": "manual_review"}, "pass_candidates": value})
    assert reason == "committed evidence invalid"


@pytest.mark.parametrize("value", _NON_FINITE, ids=["true", "numeric-string", "nan", "false", "abc"])
def test_false_rejections_reject_non_finite_values(value: object):
    score = {"start_range": {"positions": {"false_rejection": value}}}
    row = _cmp(
        "id",
        "verdicts",
        "target",
        "false_rejections",
        _false_rejections(score),
        100,
        Path("score.json"),
        at_most=True,
    )
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"


@pytest.mark.parametrize("value", _NON_FINITE, ids=["true", "numeric-string", "nan", "false", "abc"])
def test_stored_unsafe_passes_reject_non_finite_values(value: object):
    rows = _rescored_rows(
        {},
        "eth3d-facade/C",
        {"score": {"headline": {"unsafe_passes": value}}, "candidates": []},
        Path("verdict.json"),
        None,
    )
    assert any(item["note"] == "committed evidence invalid" for item in rows)


@pytest.mark.parametrize("value", _NON_FINITE, ids=["true", "numeric-string", "nan", "false", "abc"])
def test_window_overlap_rejects_non_finite_coordinates(value: object):
    assert _overlaps([value, -3.383], [-3.891, -3.383]) is False


_MISSING_SPAN = object()


@pytest.mark.parametrize(
    "span",
    [
        True,
        [True, -3.383],
        ["8.5", -3.383],
        [float("nan"), -3.383],
        [False, -3.383],
        "abc",
        [-3.891],
        [-3.891, -3.383, 1.0],
        _MISSING_SPAN,
    ],
    ids=[
        "true",
        "bool-endpoint",
        "numeric-string",
        "nan",
        "false",
        "abc",
        "one-number",
        "three-numbers",
        "missing-key",
    ],
)
def test_bad_span_fails_even_when_another_candidate_overlaps(tmp_path: Path, span: object):
    dest = tmp_path / "positive" / "eth3d-facade" / "gt-laser-gravel" / "C"
    dest.mkdir(parents=True)
    verdict = _verdict("pass")
    if span is _MISSING_SPAN:
        del verdict["candidates"][0]["start_s_m"]
    else:
        verdict["candidates"][0]["start_s_m"] = span
    planted = dest / "footprint3in.json"
    planted.write_text(json.dumps(verdict))
    config = _config_for(tmp_path, {"positive/eth3d-facade/gt-laser-gravel/C/footprint3in.json": planted})
    report = evaluate(config, tmp_path, repo=ROOT)
    row = next(item for item in report.rows if item["id"] == "positive/eth3d-facade/gt-laser-gravel/C/footprint3in")
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"
    assert exit_code(report) == 1


def _ground_strip(value: object, claims: object) -> dict:
    return {
        "metric": "false_observed_by_rule",
        "false_observed_by_rule": value,
        "rule": "ground_strip",
        "floor": 0.0247,
        "claims": claims,
    }


def _plant_ground_strip(root: Path, payload: dict | None = None) -> Path:
    folder = root / "geometry" / "eth3d-facade" / "metric"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "ground_strip.json"
    path.write_text(json.dumps(payload if payload is not None else _ground_strip(0.069, [])))
    return path


def _ground_row(root: Path, config: Path = CONFIG) -> dict:
    report = evaluate(config, root, repo=ROOT)
    return next(row for row in report.rows if row["id"] == "geometry/eth3d-facade/metric/ground_strip")


def test_committed_ground_strip_missing_is_fail(tmp_path: Path):
    row = _ground_row(tmp_path)
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"
    assert exit_code(evaluate(CONFIG, tmp_path, repo=ROOT)) == 1


def test_committed_ground_strip_malformed_metric_is_fail(tmp_path: Path):
    path = _plant_ground_strip(tmp_path, {})
    assert _ground_row(tmp_path)["status"] == "FAIL"
    assert _ground_row(tmp_path)["note"] == "committed evidence invalid"
    path.write_text(json.dumps({"false_observed_by_rule": None, "claims": []}))
    assert _ground_row(tmp_path)["status"] == "FAIL"
    path.write_text(json.dumps({"false_observed_by_rule": "0.5", "claims": []}))
    row = _ground_row(tmp_path)
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"


def test_committed_ground_strip_claims_fail(tmp_path: Path):
    planted = _plant_ground_strip(tmp_path, _ground_strip(0.069, ["w1 ground band"]))
    config = _config_for(tmp_path, {"geometry/eth3d-facade/metric/ground_strip.json": planted})
    row = _ground_row(tmp_path, config)
    assert row["status"] == "FAIL"
    assert "w1 ground band" in row["note"]
    assert "claims empty" not in row["note"]
    assert exit_code(evaluate(config, tmp_path, repo=ROOT)) == 1


def test_committed_ground_strip_off_pin_fails(tmp_path: Path):
    planted = _plant_ground_strip(tmp_path, _ground_strip(0.5, []))
    config = _config_for(tmp_path, {"geometry/eth3d-facade/metric/ground_strip.json": planted})
    row = _ground_row(tmp_path, config)
    assert row["status"] == "FAIL"
    assert "false_observed_by_rule 0.5" in row["note"]
    assert "claims empty" in row["note"]
    assert exit_code(evaluate(config, tmp_path, repo=ROOT)) == 1


def test_lamar_host_fails_when_the_measurement_disagrees(tmp_path: Path):
    folder = tmp_path / "lamar"
    folder.mkdir()
    planted = folder / "2022-02-10_s0.json"
    planted.write_text(json.dumps({"gate_host": "other-vm", "openings_p90_in": 3.75}))
    config = _config_for(tmp_path, {"lamar/2022-02-10_s0.json": planted})
    report = evaluate(config, tmp_path, repo=ROOT)
    row = next(r for r in report.rows if r["id"] == "lamar/host")
    assert row["status"] == "FAIL"
    assert row["value"] == "other-vm"
    assert row["threshold"] == "ws-11-ci"
    assert exit_code(report) == 1


def test_gravel_pass_holds_when_the_verdict_matches(tmp_path: Path):
    dest = tmp_path / "positive" / "eth3d-facade" / "gt-laser-gravel" / "C"
    dest.mkdir(parents=True)
    planted = dest / "footprint3in.json"
    planted.write_text(json.dumps(_verdict("pass")))
    config = _config_for(tmp_path, {"positive/eth3d-facade/gt-laser-gravel/C/footprint3in.json": planted})
    report = evaluate(config, tmp_path, repo=ROOT)
    row = next(r for r in report.rows if r["id"] == "positive/eth3d-facade/gt-laser-gravel/C/footprint3in")
    assert row["status"] == "PASS"


def test_allow_warn_needs_the_margin_cause(tmp_path: Path):
    dest = tmp_path / "positive" / "eth3d-facade" / "e2e" / "arkit-lidar" / "C"
    dest.mkdir(parents=True)
    (dest / "footprint3in.json").write_text(json.dumps(_verdict("manual_review", unsure_cause="margin")))
    report = evaluate(CONFIG, tmp_path, repo=ROOT)
    row = next(r for r in report.rows if r["id"] == "positive/eth3d-facade/e2e/arkit-lidar/C/footprint3in")
    assert row["status"] == "WARN"


def test_flipping_a_candidate_in_c_json_exits_nonzero(tmp_path: Path):
    """Brief §9.2: a copy of C.json with one unsure candidate flipped to pass must not exit 0.

    The gate recomputes unsafe passes from the verdict. The stored file is not consulted.
    """
    source = json.loads((ROOT / "data/fixtures/verdicts/eth3d-facade/c1-3d/C.json").read_text())
    flipped = json.loads(json.dumps(source))
    candidate = next(item for item in flipped["candidates"] if item["outcome"] == "unsure")
    candidate["outcome"] = "pass"
    flipped["score"] = {"headline": {"unsafe_passes": 0}}
    dest = tmp_path / "verdicts" / "eth3d-facade" / "c1-3d"
    dest.mkdir(parents=True)
    planted = dest / "C.json"
    planted.write_text(json.dumps(flipped))
    config = _config_for(tmp_path, {"verdicts/eth3d-facade/c1-3d/C.json": planted})
    out = tmp_path / "gates.json"
    code = main(["gates", "--config", str(config), "--inputs", str(tmp_path), "--out", str(out)])
    assert code != 0
    report = json.loads(out.read_text())
    failed = [row for row in report["rows"] if row["status"] == "FAIL" and row["metric"] == "unsafe_passes"]
    assert failed
    assert any(row["value"] and int(row["value"]) >= 1 for row in failed)


def test_decidability_reads_the_profile_share(tmp_path: Path):
    """WS-09 writes ``decidability.<profile>.share``, not a single ``decidability.share``."""
    report = tmp_path / "criteria" / "eth3d-facade" / "gt-laser" / "criteria_report.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps(
            {
                "profiles": ["A", "C"],
                "decidability": {
                    "C": {
                        "cells_total": 280,
                        "cells_decided": 169,
                        "share": 0.6054,
                        "by_engine": {
                            "engine3d_pass": 4,
                            "engine3d_fail": 12,
                            "chain_pass": 80,
                            "chain_fail": 73,
                        },
                    },
                    "A": {"cells_total": 10, "cells_decided": 1, "share": 0.10, "by_engine": {}},
                },
            }
        )
    )
    rows = _decidability_rows({"decidability": {"gt_laser_min": {"eth3d-facade": 0.38}}}, tmp_path)
    by_id = {row["id"]: row for row in rows}
    assert by_id["decidability/eth3d-facade/gt-laser/C"]["metric"] == "decidability.C.share"
    assert by_id["decidability/eth3d-facade/gt-laser/C"]["value"] == 0.6054
    assert by_id["decidability/eth3d-facade/gt-laser/C"]["status"] == "PASS"
    assert by_id["decidability/eth3d-facade/gt-laser/A"]["status"] == "FAIL"
    assert by_id["decidability/eth3d-facade/gt-laser/A"]["metric"] == "decidability.A.share"


def test_evidence_backed_pass_is_not_unsafe():
    """A schema-shaped pass with observed coverage is not an unsafe pass.

    The candidate record has no ``coverage`` field. The criterion does, and the
    contract accepts it. Counting every pass as unsafe would fail the e2e row.
    """
    verdict = {
        "site": {"decision": "pass"},
        "candidates": [{"id": "spot", "wall_id": "w1", "outcome": "pass"}],
        "criteria": [
            {
                "id": "route_length",
                "outcome": "pass",
                "candidate_id": "spot",
                "measured": {
                    "coverage": [
                        {
                            "region": "w1",
                            "status": "observed",
                            "views_median": 4,
                            "best_incidence_deg": 20,
                            "rule": {"min_views": 2, "max_incidence_deg": 80},
                        }
                    ]
                },
            }
        ],
    }
    assert _recomputed_unsafe(verdict) == 0


def test_pass_without_observed_coverage_fails(tmp_path: Path):
    folder = tmp_path / "safety" / "no_coverage"
    folder.mkdir(parents=True)
    payload = {
        "site": {"decision": "pass"},
        "candidates": [{"id": f"c{i}", "outcome": "pass", "wall_id": "w1"} for i in range(89)],
    }
    (folder / "C.json").write_text(json.dumps(payload))
    report = evaluate(CONFIG, tmp_path, repo=ROOT)
    row = next(item for item in report.rows if item["id"] == "safety/no_coverage/C")
    assert row["status"] == "FAIL"
    assert row["value"] == "pass"
    assert "no observed coverage" in row["note"]
    assert exit_code(report) == 1


def test_provisional_output_is_rejected(tmp_path: Path):
    folder = tmp_path / "geometry" / "eth3d-facade" / "arkit-lidar"
    folder.mkdir(parents=True)
    (folder / "geomscore.json").write_text(
        json.dumps({"provisional": True, "key_distances": {"by_wall": {"w1": {"p90_in": 0.1}}}})
    )
    report = evaluate(CONFIG, tmp_path, repo=ROOT)
    row = next(item for item in report.rows if item["id"] == "geometry/eth3d-facade/arkit-lidar/provisional")
    assert row["status"] == "FAIL"
    assert exit_code(report) == 1
    code = main(
        [
            "gates",
            "--config",
            str(CONFIG),
            "--inputs",
            str(ROOT / "data/fixtures"),
            "--out",
            str(tmp_path / "gates.json"),
            "--allow-missing-occluders",
        ]
    )
    assert code == 1


@pytest.mark.parametrize(
    "rel",
    [
        "verdicts/eth3d-facade/c1-3d/C.json",
        "verdicts/eth3d-facade/c1-3d/A.json",
        "labels/eth3d-facade.C.json",
        "verdicts/eth3d-facade/gt.C1.g80.json",
        "labels/eth3d-facade.demo.labels.json",
        "verdicts/eth3d-delivery/gt.C2.S.json",
        "positive/eth3d-facade/gt-laser-gravel/C/footprint3in.json",
        "positive/eth3d-facade/gt-laser-gravel/C/angle80.json",
        "criteria/eth3d-facade/gt-laser/criteria_report.json",
        "criteria/eth3d-electro/gt-laser/criteria_report.json",
        "safety/no_coverage",
        "safety/no_coverage/facade-C-c2.json",
        "safety/veto",
        "safety/veto/facade-C-0000.json",
        "lamar/2022-02-10_s0.json",
        "geometry/eth3d-facade/metric/ground_strip.json",
    ],
)
def test_deleting_a_committed_input_fails_closed(tmp_path: Path, rel: str):
    inputs = tmp_path / "inputs"
    shutil.copytree(FIXTURES, inputs)
    target = inputs / rel
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    base = evaluate(CONFIG, FIXTURES, repo=ROOT)
    report = evaluate(CONFIG, inputs, repo=ROOT)
    assert exit_code(report) == 1
    assert any(row["status"] == "FAIL" and row["note"] == "committed evidence invalid" for row in report.rows)
    after = {row["id"]: row for row in report.rows}
    for row in base.rows:
        assert row["id"] in after
        updated = after[row["id"]]
        if row["status"] != "SKIP":
            assert updated["status"] != "SKIP"
        if row["evidence_path"] and updated["evidence_path"]:
            assert Path(updated["evidence_path"]).name == Path(row["evidence_path"]).name
        if updated["evidence_path"]:
            assert str(FIXTURES) not in updated["evidence_path"]


def test_extra_veto_file_is_committed_evidence_invalid(tmp_path: Path):
    inputs = tmp_path / "inputs"
    shutil.copytree(FIXTURES, inputs)
    extra = inputs / "safety/veto/facade-C-0080.json"
    extra.write_text("{}")
    report = evaluate(CONFIG, inputs, repo=ROOT)
    row = next(item for item in report.rows if item["id"].endswith("facade-C-0080.json"))
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"
    assert exit_code(report) == 1


def test_malformed_c1_verdict_does_not_score_another_copy(tmp_path: Path):
    inputs = tmp_path / "inputs"
    shutil.copytree(FIXTURES, inputs)
    (inputs / "verdicts/eth3d-facade/c1-3d/C.json").write_text("{")
    report = evaluate(CONFIG, inputs, repo=ROOT)
    row = next(item for item in report.rows if item["id"] == "positive/eth3d-facade/gt-laser/C/footprint3in")
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"
    assert Path(row["evidence_path"]).name == "C.json"
    assert str(FIXTURES) not in row["evidence_path"]
    demo = next(item for item in report.rows if item["id"] == "verdict/eth3d-facade/demo/unsafe_passes")
    assert Path(demo["evidence_path"]).name == "gt.C1.g80.json"
    assert exit_code(report) == 1


def _copied_fixtures(tmp_path: Path) -> Path:
    inputs = tmp_path / "inputs"
    shutil.copytree(FIXTURES, inputs)
    return inputs


def test_empty_no_coverage_verdict_is_invalid(tmp_path: Path):
    inputs = _copied_fixtures(tmp_path)
    (inputs / "safety/no_coverage/facade-C-c2.json").write_text("{}")
    report = evaluate(CONFIG, inputs, repo=ROOT)
    row = next(item for item in report.rows if item["id"] == "safety/no_coverage/facade-C-c2")
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"
    assert row["value"] is None
    assert exit_code(report) == 1


def test_null_lamar_openings_metric_is_invalid(tmp_path: Path):
    inputs = _copied_fixtures(tmp_path)
    path = inputs / "lamar/2022-02-10_s0.json"
    measured = json.loads(path.read_text())
    measured["openings_p90_in"] = None
    path.write_text(json.dumps(measured))
    report = evaluate(CONFIG, inputs, repo=ROOT)
    row = next(item for item in report.rows if item["id"] == "lamar/2022-02-10_s0/openings_p90_in")
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"
    assert exit_code(report) == 1
    measured.pop("openings_p90_in")
    path.write_text(json.dumps(measured))
    report = evaluate(CONFIG, inputs, repo=ROOT)
    row = next(item for item in report.rows if item["id"] == "lamar/2022-02-10_s0/openings_p90_in")
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"


def test_boolean_metrics_are_not_numbers(tmp_path: Path):
    inputs = _copied_fixtures(tmp_path)
    phone = inputs / "lamar/2022-02-10_s0.json"
    measured = json.loads(phone.read_text())
    measured["openings_p90_in"] = False
    phone.write_text(json.dumps(measured))
    report = evaluate(CONFIG, inputs, repo=ROOT)
    row = next(item for item in report.rows if item["id"] == "lamar/2022-02-10_s0/openings_p90_in")
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"
    assert row["value"] is None
    criteria = inputs / "criteria/eth3d-facade/gt-laser/criteria_report.json"
    document = json.loads(criteria.read_text())
    document["decidability"]["C"]["share"] = True
    criteria.write_text(json.dumps(document))
    phone.write_text(json.dumps({**measured, "openings_p90_in": 3.75}))
    report = evaluate(CONFIG, inputs, repo=ROOT)
    share = next(item for item in report.rows if item["id"] == "decidability/eth3d-facade/gt-laser/C")
    assert share["status"] == "FAIL"
    assert share["note"] == "committed evidence invalid"
    assert share["value"] != 1.0
    assert exit_code(report) == 1


def test_empty_labels_object_is_invalid_not_a_crash(tmp_path: Path):
    inputs = _copied_fixtures(tmp_path)
    (inputs / "labels/eth3d-facade.C.json").write_text("{}")
    report = evaluate(CONFIG, inputs, repo=ROOT)
    failed = [
        item
        for item in report.rows
        if item["id"].startswith("verdict/eth3d-facade/C/") and item["note"] == "committed evidence invalid"
    ]
    assert failed
    assert exit_code(report) == 1


def test_same_name_bytes_from_another_verdict_are_invalid(tmp_path: Path):
    inputs = _copied_fixtures(tmp_path)
    g80 = inputs / "verdicts/eth3d-facade/gt.C1.g80.json"
    fp3 = FIXTURES / "verdicts/eth3d-facade/gt.C1.fp3.json"
    g80.write_bytes(fp3.read_bytes())
    report = evaluate(CONFIG, inputs, repo=ROOT)
    row = next(item for item in report.rows if item["id"] == "verdict/eth3d-facade/demo/unsafe_passes")
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"
    assert str(row["evidence_path"]).endswith("gt.C1.g80.json")
    assert exit_code(report) == 1


def test_refresh_rewrites_pins_after_a_committed_edit(tmp_path: Path):
    inputs = _copied_fixtures(tmp_path)
    phone = inputs / "lamar/2022-02-10_s0.json"
    payload = json.loads(phone.read_text())
    payload["note"] = "rewritten overlay"
    phone.write_text(json.dumps(payload))
    before = evaluate(CONFIG, inputs, repo=ROOT)
    row = next(item for item in before.rows if item["id"] == "lamar/2022-02-10_s0/openings_p90_in")
    assert row["status"] == "FAIL"
    assert row["note"] == "committed evidence invalid"
    assert exit_code(before) == 1
    config = tmp_path / "gates.yaml"
    shutil.copy(CONFIG, config)
    refresh_committed_hashes(config, inputs)
    after = evaluate(config, inputs, repo=ROOT)
    assert after.summary["fail"] == 0
    assert exit_code(after) == 0


def test_e2e_with_api_url_uses_the_table_exit_code(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CAPTURE_API_URL", "http://127.0.0.1:9")
    code = e2e_main(["--config", str(CONFIG), "--inputs", str(ROOT / "data/fixtures"), "--out", str(tmp_path / "e2e")])
    assert code == 2
