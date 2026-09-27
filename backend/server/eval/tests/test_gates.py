"""Gate rows fail closed. Missing engines SKIP; a bad verdict is a FAIL and exit 1."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import yaml

from server.eval.cli import main
from server.eval.e2e import main as e2e_main
from server.eval.gates import (
    SPAN_TOL_M,
    _apply_known_failure,
    _decidability_rows,
    _recomputed_unsafe,
    _row,
    evaluate,
    exit_code,
)

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "server" / "eval" / "gates.yaml"


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


def _score(unsafe: int) -> dict:
    return {
        "headline": {"unsafe_passes": unsafe, "pass_recall": None, "labelled_pass_positions": 0},
        "start_range": {"positions": {"false_rejection": 0}},
        "site": {"criteria": []},
        "coverage_contract_errors": [],
        "c5_margin_errors": [],
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
    angle = json.loads(
        (ROOT / "data/fixtures/positive/eth3d-facade/gt-laser-gravel/C/angle80.json").read_text()
    )
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
    target = inputs / "verdict-scores" / "eth3d-facade"
    target.mkdir(parents=True)
    (target / "C.json").write_text(json.dumps(_score(1)))
    out = tmp_path / "gates.json"
    code = main(["gates", "--config", str(CONFIG), "--inputs", str(inputs), "--out", str(out)])
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
    (dest / "footprint3in.json").write_text(json.dumps(_verdict("manual_review")))
    report = evaluate(CONFIG, tmp_path, repo=ROOT)
    row = next(r for r in report.rows if r["id"] == "positive/eth3d-facade/gt-laser-gravel/C/footprint3in")
    assert row["status"] == "FAIL"
    assert row["value"] == "manual_review"
    assert exit_code(report) == 1


def test_route_over_the_bar_is_known_until_expiry(tmp_path: Path):
    dest = tmp_path / "positive" / "eth3d-facade" / "gt-laser-gravel" / "C"
    dest.mkdir(parents=True)
    verdict = _verdict("pass")
    verdict["criteria"][0]["measured"]["value_ft"] = 9.1834
    (dest / "footprint3in.json").write_text(json.dumps(verdict))
    _plant_ground_strip(tmp_path)
    report = evaluate(CONFIG, tmp_path, repo=ROOT)
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
    (dest / "footprint3in.json").write_text(json.dumps(verdict))
    report = evaluate(CONFIG, tmp_path, repo=ROOT)
    row = next(r for r in report.rows if r["id"] == "positive/eth3d-facade/gt-laser-gravel/C/footprint3in")
    assert row["status"] == "FAIL"
    assert "route 14.0 ft exceeds 9.0" in row["note"]
    assert "known failure" not in row["note"]
    assert report.summary["known_fail"] == 0
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


def _ground_row(root: Path) -> dict:
    report = evaluate(CONFIG, root, repo=ROOT)
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
    _plant_ground_strip(tmp_path, _ground_strip(0.069, ["w1 ground band"]))
    row = _ground_row(tmp_path)
    assert row["status"] == "FAIL"
    assert "w1 ground band" in row["note"]
    assert "claims empty" not in row["note"]
    assert exit_code(evaluate(CONFIG, tmp_path, repo=ROOT)) == 1


def test_committed_ground_strip_off_pin_fails(tmp_path: Path):
    _plant_ground_strip(tmp_path, _ground_strip(0.5, []))
    row = _ground_row(tmp_path)
    assert row["status"] == "FAIL"
    assert "false_observed_by_rule 0.5" in row["note"]
    assert "claims empty" in row["note"]
    assert exit_code(evaluate(CONFIG, tmp_path, repo=ROOT)) == 1


def test_lamar_host_fails_when_the_measurement_disagrees(tmp_path: Path):
    folder = tmp_path / "lamar"
    folder.mkdir()
    (folder / "2022-02-10_s0.json").write_text(json.dumps({"gate_host": "other-vm", "openings_p90_in": 3.75}))
    report = evaluate(CONFIG, tmp_path, repo=ROOT)
    row = next(r for r in report.rows if r["id"] == "lamar/host")
    assert row["status"] == "FAIL"
    assert row["value"] == "other-vm"
    assert row["threshold"] == "ws-11-ci"
    assert exit_code(report) == 1


def test_gravel_pass_holds_when_the_verdict_matches(tmp_path: Path):
    dest = tmp_path / "positive" / "eth3d-facade" / "gt-laser-gravel" / "C"
    dest.mkdir(parents=True)
    (dest / "footprint3in.json").write_text(json.dumps(_verdict("pass")))
    report = evaluate(CONFIG, tmp_path, repo=ROOT)
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
    (dest / "C.json").write_text(json.dumps(flipped))
    out = tmp_path / "gates.json"
    code = main(["gates", "--config", str(CONFIG), "--inputs", str(tmp_path), "--out", str(out)])
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


def test_e2e_with_api_url_uses_the_table_exit_code(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CAPTURE_API_URL", "http://127.0.0.1:9")
    code = e2e_main(["--config", str(CONFIG), "--inputs", str(ROOT / "data/fixtures"), "--out", str(tmp_path / "e2e")])
    assert code == 2
