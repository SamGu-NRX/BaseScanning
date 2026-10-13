"""Regression cases for unresolved scoring comments on merged PR #4."""

import json
import os
import zipfile
from decimal import Decimal

import pytest
from helpers import FIXTURES, SESSION_ZIP, reported, survey, threshold
from test_measure_lab import Setup

from scoring.cli import main
from scoring.inputs import Check, InputError, load_rules, load_truth
from scoring.measure_lab import load_session
from scoring.metrics import score_check, score_measurement
from scoring.report import CSV_NAMES, _table, _table_code

RULES = FIXTURES / "rules.json"
TRUTH = FIXTURES / "truth" / "synthetic-01.json"
RESULT = FIXTURES / "results" / "ar-taps.json"
MAP = FIXTURES / "measure-lab" / "map.json"


@pytest.mark.parametrize("session_id", ["m3", "m6"])
def test_duplicate_session_measurements_are_rejected(tmp_path, session_id):
    setup = Setup(tmp_path)
    setup.map["measurements"]["c1-gas"]["session_measurement"] = session_id
    setup.map["measurements"]["c2-gas"]["session_measurement"] = session_id
    assert "distinct survey distances need distinct session measurements" in setup.error()


def test_capture_duration_includes_last_refusal(tmp_path):
    setup = Setup(tmp_path)
    setup.session["refusals"][0]["time"] = 1500.75
    assert setup.run()["timing"]["capture_s"] == Decimal("500.500")


def test_refusal_only_session_has_capture_duration(tmp_path):
    setup = Setup(tmp_path)
    setup.session["measurements"] = []
    setup.map["measurements"] = {
        survey_id: "unsupported" for survey_id in setup.map["measurements"]
    }
    assert setup.run()["timing"]["capture_s"] == Decimal("299.750")


def test_refusal_before_start_is_rejected(tmp_path):
    setup = Setup(tmp_path)
    setup.session["refusals"][0]["time"] = 999
    assert "capture attempt predates startedAtUptime" in setup.error()


@pytest.mark.parametrize("failure", [NotImplementedError, RuntimeError, OSError, EOFError])
def test_unreadable_zip_member_is_input_error(tmp_path, monkeypatch, failure):
    truth = load_truth(TRUTH, load_rules(RULES))

    def unreadable(*args, **kwargs):
        raise failure("unreadable compression")

    monkeypatch.setattr(zipfile.ZipFile, "open", unreadable)
    with pytest.raises(InputError, match=r"cannot read session\.json"):
        load_session(SESSION_ZIP, truth)
    args = [str(SESSION_ZIP), "--map", str(MAP), "--rules", str(RULES), "--truth", str(TRUTH)]
    out = tmp_path / "out.json"
    assert main(["import-measure-lab", *args, "--out", str(out)]) == 2
    assert not out.exists()


@pytest.mark.parametrize("missing", ["failed", "unsupported"])
def test_unknown_survey_missing_evidence_has_justified_abstention(missing):
    measurement = score_measurement(
        survey(None, None, status="not_measured"),
        reported(None, None, missing=missing),
        scale_reference=False,
    )
    score = score_check(
        Check("c1", "gas", "m", "gas_clearance_ft"), threshold(), measurement, "unsure"
    )
    assert score.truth == "unknown"
    assert score.agrees is None
    assert score.unsafe_pass is None
    assert score.abstention == "justified"


@pytest.mark.parametrize("backslashes", range(1, 5))
def test_table_keeps_backslashes_before_pipes(backslashes):
    value = "p" + "\\" * backslashes + "|x"
    rendered = _table(["Pipeline"], [[_table_code(value)]])[-1]
    assert rendered == "| <code>&#112;" + "&#92;" * backslashes + "&#124;&#120;</code> |"
    assert rendered.count("|") == 2


def test_backslash_code_cell_cannot_inject_inline_markdown():
    rendered = _table(["Pipeline"], [[_table_code("\\`x`*y*_z_<b>")]])[-1]
    assert rendered == (
        "| <code>&#92;&#96;&#120;&#96;&#42;&#121;&#42;&#95;&#122;&#95;&#60;&#98;&#62;</code> |"
    )


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
@pytest.mark.parametrize("first,second", [(0, 1), (0, 2), (1, 2)])
def test_csv_destination_aliases_are_rejected(tmp_path, capsys, link_kind, first, second):
    out = tmp_path / "out"
    out.mkdir()
    target = out / CSV_NAMES[first]
    target.write_bytes(b"original output")
    alias = out / CSV_NAMES[second]
    if link_kind == "symlink":
        alias.symlink_to(target)
    else:
        os.link(target, alias)
    args = ["--rules", str(RULES), "--truth", str(TRUTH), "--results", str(RESULT)]
    assert main([*args, "--out", str(out)]) == 2
    captured = capsys.readouterr()
    assert "aliases output" in captured.err
    assert captured.out == ""
    assert target.read_bytes() == alias.read_bytes() == b"original output"
    assert not (out / CSV_NAMES[3 - first - second]).exists()


def test_all_report_run_labels_include_capture(tmp_path, capsys):
    truth = json.loads(TRUTH.read_text())
    truth["captures"].append("second-walk")
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(json.dumps(truth))
    run = json.loads(RESULT.read_text())
    run["capture"] = "second-walk"
    result_path = tmp_path / "second.json"
    result_path.write_text(json.dumps(run))
    args = [
        "--rules",
        str(RULES),
        "--truth",
        str(truth_path),
        "--results",
        str(RESULT),
        str(result_path),
        "--out",
        str(tmp_path / "out"),
    ]
    assert main(args) == 0
    lines = capsys.readouterr().out.splitlines()
    for capture in ("synthetic-01-walk-1", "second-walk"):
        rows = [line for line in lines if line.startswith("| `ar-taps`") and f"`{capture}`" in line]
        assert len(rows) == 3
        findings = [
            line
            for line in lines
            if line.startswith("- `ar-taps`") and f"Capture `{capture}`." in line
        ]
        assert len(findings) == 5
