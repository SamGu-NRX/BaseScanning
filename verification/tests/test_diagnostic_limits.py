import json

import pytest
from test_judge_meter import META

from hsverify import e2e
from hsverify.diagnostics import (
    MAX_DIAGNOSTIC_CHARACTERS,
    MAX_DIAGNOSTIC_COUNT,
    MAX_EXPECTATION_EXAMPLES,
    DiagnosticMessages,
)
from hsverify.resultcheck import expectation_problems


def rejected_runs(count):
    return {
        "decision": "reject",
        "sweep": [
            {
                "wall_id": "w1",
                "start_ft": [i, i + 1],
                "outcome": "fail",
                "failing": [],
                "unsure": [],
            }
            for i in range(count)
        ],
    }


def test_repeated_sweep_failures_report_reason_once(tmp_path):
    reason = "r" * 4_096
    expected = {
        "sweep_runs": [
            {"wall_id": "w1", "start_ft": [0, 1001], "outcome_not": "fail", "reason": reason}
        ]
    }
    problems = expectation_problems(expected, rejected_runs(1_000))
    assert len(problems) == 1
    assert "failed for 1000 runs" in problems[0]
    assert problems[0].count(reason) == 1
    assert problems[0].count(" is fail") == MAX_EXPECTATION_EXAMPLES
    records = [
        {
            "name": "many runs",
            "status": "fail",
            "expectation_problems": problems,
            "problems": problems,
        }
    ]
    report = e2e.write_report(tmp_path, META, records)
    assert len((tmp_path / "report.json").read_text()) < MAX_DIAGNOSTIC_CHARACTERS
    assert len((tmp_path / "report.md").read_text()) < MAX_DIAGNOSTIC_CHARACTERS
    assert not report["all_passed"]


@pytest.mark.parametrize(
    "message",
    ["bad", "x" * 4_096, "\0" * 4_000, "🦉" * 4_000],
    ids=["count", "ascii", "controls", "unicode"],
)
def test_one_report_budget_covers_every_case_and_duplicate_field(tmp_path, message):
    records = [
        {
            "name": str(i),
            "status": "fail",
            "decision": "reject",
            "problems": [message],
            "contract_problems": [message],
        }
        for i in range(MAX_DIAGNOSTIC_COUNT)
    ]
    report = e2e.write_report(tmp_path, META, records)
    messages = [
        m for r in report["records"] for key in ("problems", "contract_problems") for m in r[key]
    ]
    messages += report["diagnostic_summary"]
    assert len(messages) <= MAX_DIAGNOSTIC_COUNT
    assert sum(len(json.dumps(message)) for message in messages) <= MAX_DIAGNOSTIC_CHARACTERS
    kept = len(messages) - 1
    assert report["diagnostic_summary"] == [f"further problems omitted ({2 * len(records) - kept})"]
    assert (tmp_path / "report.md").read_text().count("further problems omitted (") == 1
    assert (tmp_path / "report.json").read_text().count("further problems omitted (") == 1
    assert report["contract_problem_count"] == len(records)
    assert not report["contract_ok"]
    assert all(r["problems"] == [message] for r in records)


def test_oversized_reason_is_omitted_before_accumulating_output():
    expected = {
        "sweep_runs": [
            {
                "wall_id": "w1",
                "start_ft": [0, 1001],
                "outcome_not": "fail",
                "reason": "r" * (3 * 1024 * 1024),
            }
        ]
    }
    assert expectation_problems(expected, rejected_runs(1_000)) == ["further problems omitted (1)"]


def test_repeated_check_failures_are_aggregated_per_expectation():
    result = {
        "decision": "reject",
        "checks": [{"id": f"check-{i}", "outcome": "fail"} for i in range(2_000)],
    }
    problems = expectation_problems({"checks": [{"match": "check", "outcome": "pass"}]}, result)
    assert len(problems) <= MAX_DIAGNOSTIC_COUNT
    assert sum(map(len, problems)) <= MAX_DIAGNOSTIC_CHARACTERS
    assert len(problems) == 1
    assert "failed for 2000 checks" in problems[0]
    assert problems[0].count("expected pass") == MAX_EXPECTATION_EXAMPLES


def test_report_coalesces_preexisting_omission_summaries(tmp_path):
    collector = DiagnosticMessages()
    collector.append("x" * MAX_DIAGNOSTIC_CHARACTERS)
    records = [{"name": str(i), "status": "fail", "problems": collector.finish()} for i in range(3)]
    report = e2e.write_report(tmp_path, META, records)
    assert report["diagnostic_summary"] == ["further problems omitted (3)"]
    assert json.dumps(report).count("further problems omitted (") == 1


def test_diagnostic_budget_stops_formatting_after_exhaustion():
    messages = DiagnosticMessages()
    messages.append("x" * MAX_DIAGNOSTIC_CHARACTERS)

    class ForbiddenFormat:
        def __format__(self, spec):
            raise AssertionError("formatting continued after diagnostic budget exhaustion")

    messages.append("{}", ForbiddenFormat())
    assert messages.finish() == ["further problems omitted (2)"]


@pytest.mark.parametrize("field", ["contract_problems", "expectation_problems"])
def test_markdown_keeps_first_explanation_when_duplicate_fields_fill_budget(tmp_path, field):
    problems = [f"explanation {i}" for i in range(1_000)]
    record = {
        "name": "many failures",
        "status": "fail",
        "decision": "reject",
        "problems": problems,
        field: problems,
    }
    report = e2e.write_report(tmp_path, META, [record])
    row = next(
        line
        for line in (tmp_path / "report.md").read_text().splitlines()
        if line.startswith("| many failures |")
    )
    assert "explanation 0" in row
    retained = [
        message
        for item in report["records"]
        for key in ("problems", field)
        for message in item[key]
    ]
    retained += report["diagnostic_summary"]
    assert len(retained) <= MAX_DIAGNOSTIC_COUNT
    assert sum(len(json.dumps(message)) for message in retained) <= MAX_DIAGNOSTIC_CHARACTERS
    assert not report["all_passed"]
