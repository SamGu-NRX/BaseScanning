"""The suite itself: broken stages FAIL rather than SKIP, thresholds come only
from the documented bars, and reports land as suite.json + SUMMARY.md."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from server.eval.adapters.criteria import CriteriaConfig, evaluate_scene
from server.eval.coverage import MissingOccluderError
from server.eval.suite import _engine_config, _matches_reference, _reference, run_suite
from server.eval.tests.flat_wall import write as write_flat_wall

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "data" / "fixtures"
CONFIG = ROOT / "server" / "eval" / "gates.yaml"


def _run(config: Path, inputs: Path, out: Path) -> dict:
    report = run_suite(config, inputs, out, repo=ROOT)
    assert (out / "suite.json").is_file()
    assert (out / "SUMMARY.md").is_file()
    assert json.loads((out / "suite.json").read_text())["format"] == "hss-eval-suite/v0"
    return report


def _config_for(tmp_path: Path, overrides: dict) -> Path:
    cfg = yaml.safe_load(CONFIG.read_text()) or {}
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    dest = tmp_path / "suite.yaml"
    dest.write_text(yaml.safe_dump(cfg))
    return dest


def test_broken_engine_fails_rather_than_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A stage that cannot run is a FAIL row with the error, never a SKIP."""

    def broken(*args, **kwargs):
        raise ImportError("not_merged:ws-09")

    monkeypatch.setattr("server.eval.suite.evaluate_scene", broken)
    report = _run(CONFIG, FIXTURES, tmp_path / "out")
    pipeline = [row for row in report["rows"] if row["id"].startswith("pipeline/")]
    assert pipeline
    for row in pipeline:
        assert row["status"] == "FAIL"
        assert "not_merged:ws-09" in row["note"]
    # Geometry rows fail closed on this machine too (no PLYs): the broken run
    # must produce more failures than the pipeline stage alone, never SKIPs.
    assert report["summary"]["fail"] >= len(pipeline)
    assert not any(row["status"] == "SKIP" for row in report["rows"])
    # Downstream stages fail closed too: no verdict was produced, so nothing is
    # silently unscored.
    assert any(row["id"].startswith("verdict/") and row["status"] == "FAIL" for row in report["rows"])


def test_suite_passes_on_synthetic_site(tmp_path: Path):
    """Green path on a synthetic scene: engine runs, labels score, exit would be 0."""
    inputs = tmp_path / "inputs"
    scene = write_flat_wall(inputs / "scenes" / "flat-wall" / "gt-laser")
    probe = evaluate_scene(
        scene,
        tmp_path / "probe",
        config=CriteriaConfig(profiles=("C",), ground_rule="angle80", c2=False, c3=False),
    )
    decision = probe["C"]["site"]["decision"]
    labels_dir = inputs / "labels"
    labels_dir.mkdir(parents=True)
    (labels_dir / "flat-wall.C.json").write_text(
        json.dumps(
            {
                "format": "base-labels/v0",
                "site": "synthetic-flatwall",
                "rules_profile": "C",
                "site_decision": decision,
                "labels": [],
            }
        )
        + "\n"
    )
    config = _config_for(
        tmp_path,
        {
            "sites": {"flat-wall": {"labels": ["C"]}},
            "suite": {"criteria": {"profiles": ["C"], "ground_rule": "angle80", "c2": False}},
            "verdicts": {
                "unsafe_passes_max": 0,
                "false_rejections_max": 0,
                "coverage_contract_errors_max": 0,
                "c5_margin_errors_max": 0,
            },
        },
    )
    report = _run(config, inputs, tmp_path / "out")
    by_id = {row["id"]: row for row in report["rows"]}
    assert by_id["pipeline/flat-wall"]["status"] == "PASS"
    assert by_id["pipeline/flat-wall"]["value"]["C"] == decision
    assert by_id["criteria/flat-wall/report"]["status"] == "MEASURED"
    verdict = by_id["verdict/flat-wall/C"]
    assert verdict["status"] == "PASS"
    assert verdict["value"]["unsafe_passes"] == 0
    assert report["summary"]["fail"] == 0


def test_missing_recon_asset_fails_with_a_produce_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A gitignored recon PLY is a FAIL row that names how to produce it."""

    def missing(*args, **kwargs):
        raise MissingOccluderError(
            "recon asset points.ply is not a readable file under data/fixtures/scenes/eth3d-facade/colmap-oracle"
        )

    monkeypatch.setattr("server.eval.suite.score_scene", missing)
    report = _run(CONFIG, FIXTURES, tmp_path / "out")
    geometry = [row for row in report["rows"] if row["id"].startswith("geometry/")]
    assert geometry
    for row in geometry:
        assert row["status"] == "FAIL"
        assert "scripts/modal_scene.py" in row["note"]
    assert not any(row["status"] == "SKIP" for row in report["rows"])


def test_engine_config_defaults_to_the_documented_run():
    cfg = _engine_config({})
    assert cfg.profiles == ("A", "B", "C", "S", "public")
    assert cfg.ground_rule == "footprint3in"
    assert cfg.c2 is True
    assert cfg.c3 is False


def test_matches_reference_compares_at_published_precision():
    reference = {
        "overall": {"median_in": 0.63, "p90_in": 1.94, "max_in": 2.67},
        "scale_error_pct": -0.447,
        "by_wall_p90_in": 1.94,
    }
    measured = {
        "overall": {"median_in": 0.6317, "p90_in": 1.9402, "max_in": 2.6749},
        "scale": {"scale_error_pct": -0.4471},
        "by_wall": {"w1": {"p90_in": 1.9381}},
    }
    ok, diffs = _matches_reference(measured, reference)
    assert ok, diffs
    ok, diffs = _matches_reference({**measured, "overall": {**measured["overall"], "p90_in": 2.5}}, reference)
    assert not ok
    assert any("p90_in" in diff for diff in diffs)


def test_reference_prefers_the_visibility_remeasure(tmp_path: Path):
    ref_dir = tmp_path / "reference-scores" / "site"
    ref_dir.mkdir(parents=True)
    (ref_dir / "geomscore-old.json").write_text(
        json.dumps({"recon_scene_id": "site/recon-a", "key_distances": {"overall": {"median_in": 9.0}}, "scale": {}})
    )
    (ref_dir / "rebaseline-false.json").write_text(
        json.dumps(
            {"method": "score_scene(visibility=False)", "rows": [{"recon": "recon-a", "overall": {"median_in": 1.0}}]}
        )
    )
    (ref_dir / "rebaseline-true.json").write_text(
        json.dumps(
            {
                "method": "score_scene(visibility=True)",
                "rows": [{"recon": "recon-a", "overall": {"median_in": 2.0}, "by_wall": {"w1": {"p90_in": 3.0}}}],
            }
        )
    )
    reference = _reference(tmp_path, "site", "recon-a")
    assert reference["overall"]["median_in"] == 2.0
    assert reference["by_wall_p90_in"] == 3.0
    assert reference["visibility"] is True


def test_committed_fixtures_suite(tmp_path: Path):
    """Full run on the real fixtures when this machine has the GT PLYs (make gt)."""
    gt = ROOT / ".fixtures" / "gt" / "eth3d-facade" / "gt-laser"
    if not (gt / "laser.ply").is_file() or not (gt / "occluders.ply").is_file():
        pytest.skip("GT PLYs absent; run make gt for the full-fixture run")
    has_recon_assets = bool(list((FIXTURES / "scenes").glob("*/colmap-*/points.ply")))
    report = _run(CONFIG, FIXTURES, tmp_path / "out")
    failed = [row for row in report["rows"] if row["status"] == "FAIL"]
    if not has_recon_assets:
        # The recon PLYs are gitignored Modal-pipeline outputs. On a checkout
        # without them the geometry rows fail closed with the produce hint and
        # every other stage must still be green.
        assert failed and all(
            row["id"].startswith("geometry/") and "scripts/modal_scene.py" in row["note"] for row in failed
        ), json.dumps(failed, indent=1, default=str)
        assert report["summary"]["pass"] > 0
        return
    assert not failed, json.dumps(failed, indent=1, default=str)
    assert report["summary"]["pass"] > 0


RECEIPT_STATUSES = ("PASS", "FAIL", "WARN", "MEASURED")
SCORE_KEYS = {
    "unsafe_passes",
    "false_rejections",
    "coverage_contract_errors",
    "c5_margin_errors",
    "site_agreement",
    "pass_recall",
    "manual_review_rate",
}


def _assert_receipt_contract(report: dict) -> None:
    """The suite.json seam a machine consumes: a closed status vocabulary with no
    SKIP, summary tallies that match the rows, a reason on every row that is not
    a pass, and a fixed set of score keys on every verdict row that scored."""
    rows = report["rows"]
    assert all(row["status"] in RECEIPT_STATUSES for row in rows), sorted({row["status"] for row in rows})
    assert not any(row["status"] == "SKIP" for row in rows)
    for status in RECEIPT_STATUSES:
        assert report["summary"][status.lower()] == sum(row["status"] == status for row in rows)
    for row in rows:
        if row["status"] != "PASS":
            assert (row.get("note") or "").strip(), f"{row['id']} carries no reason"
    for row in (r for r in rows if r["group"] == "verdicts" and r["status"] == "MEASURED"):
        assert row["value"] is None, row["id"]
        assert "no committed labels" in row["note"], row["note"]
    for row in (r for r in rows if r["group"] == "verdicts" and r["value"] is not None):
        assert set(row["value"]) == SCORE_KEYS, row["id"]


def test_receipt_distinguishes_pass_fail_and_not_scored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """suite.json alone separates pass, fail, skip-equivalent and blocked rows.

    Not-scored rows are MEASURED with a "no committed ..." reason; blocked
    fixture rows are FAIL rows whose note names the missing asset and how to
    produce it. Parsing SUMMARY.md is never required to tell them apart.
    """

    def broken(*args, **kwargs):
        raise ImportError("not_merged:ws-09")

    monkeypatch.setattr("server.eval.suite.evaluate_scene", broken)
    report = _run(CONFIG, FIXTURES, tmp_path / "out")
    _assert_receipt_contract(report)
    by_group: dict[str, list[dict]] = {}
    for row in report["rows"]:
        by_group.setdefault(row["group"], []).append(row)
    # pass: fixture rows that are present and schema-valid
    assert all(row["status"] == "PASS" for row in by_group["fixtures"])
    # blocked: the engine cannot run, and every downstream row says why
    assert all(row["status"] == "FAIL" and "criteria engine failed" in row["note"] for row in by_group["pipeline"])
    # skip-equivalent: the public profile has no committed labels, so it is
    # MEASURED (recorded, no bar) rather than FAIL or silently omitted
    assert all(
        row["id"].endswith("/public") and row["status"] == "MEASURED"
        for row in by_group["verdicts"]
        if row["id"].endswith("/public")
    )
    assert any(row["status"] == "FAIL" for row in by_group["verdicts"] if not row["id"].endswith("/public"))
    # same contract on the green path: scored verdict rows keep the score keys
    monkeypatch.undo()
    inputs = tmp_path / "synthetic"
    scene = write_flat_wall(inputs / "scenes" / "flat-wall" / "gt-laser")
    probe = evaluate_scene(
        scene,
        tmp_path / "probe",
        config=CriteriaConfig(profiles=("C",), ground_rule="angle80", c2=False, c3=False),
    )
    labels_dir = inputs / "labels"
    labels_dir.mkdir(parents=True)
    (labels_dir / "flat-wall.C.json").write_text(
        json.dumps(
            {
                "format": "base-labels/v0",
                "site": "synthetic-flatwall",
                "rules_profile": "C",
                "site_decision": probe["C"]["site"]["decision"],
                "labels": [],
            }
        )
        + "\n"
    )
    config = _config_for(
        tmp_path,
        {
            "sites": {"flat-wall": {"labels": ["C"]}},
            "suite": {"criteria": {"profiles": ["C"], "ground_rule": "angle80", "c2": False}},
            "verdicts": {
                "unsafe_passes_max": 0,
                "false_rejections_max": 0,
                "coverage_contract_errors_max": 0,
                "c5_margin_errors_max": 0,
            },
        },
    )
    green = _run(config, inputs, tmp_path / "green")
    _assert_receipt_contract(green)
    assert green["summary"]["fail"] == 0
    verdict = next(row for row in green["rows"] if row["id"] == "verdict/flat-wall/C")
    assert verdict["status"] == "PASS" and verdict["value"]["unsafe_passes"] == 0
