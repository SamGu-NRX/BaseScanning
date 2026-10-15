"""The committed synthetic fixtures, scored through the CLI and checked against hand-worked answers.

House synthetic-01 has two spots. Survey outcomes under fixtures/rules.json:
Passing is strict. Route checks pass below review_route_ft (15), fail beyond max_route_ft (20),
and are review from one line to the other, both included.
c1: gas pass (4.5 vs 3), facing_gap pass (5.0 vs 3), route pass (12.05 < 15), pool pass (absent).
c2: gas borderline (3.02 +- 0.03 vs 3), facing_gap fail (2.5 vs 3), route review (19.9 +- 0.05
is past 15 but not past 20), pool unknown (not measured).
"""

import csv
from pathlib import Path

import pytest
from helpers import FIXTURES

from scoring.cli import main

RUNS = ["ar-taps", "photo-depth", "mesh-scaled"]


def run_cli(tmp_path: Path, *extra: str) -> int:
    return main(
        [
            "--rules",
            str(FIXTURES / "rules.json"),
            "--truth",
            str(FIXTURES / "truth" / "synthetic-01.json"),
            "--results",
            *(str(FIXTURES / "results" / f"{name}.json") for name in RUNS),
            "--out",
            str(tmp_path),
            *extra,
        ]
    )


@pytest.fixture
def scored(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    assert run_cli(tmp_path) == 0
    output = capsys.readouterr()

    def table(name: str) -> list[dict[str, str]]:
        with (tmp_path / name).open(newline="") as handle:
            return list(csv.DictReader(handle))

    return output, table


def by_key(rows: list[dict[str, str]], *keys: str) -> dict[tuple[str, ...], dict[str, str]]:
    return {tuple(row[key] for key in keys): row for row in rows}


# Abs errors in inches, per run: see each results fixture.
# ar-taps:     wall 2.4, c1-gas 2.4, c1-facing 4.8, c1-route 3.0, c2-gas 3.96, c2-route 4.8
# ar-taps passes c2 facing_gap and fails c2 pool with both measurements missing as failed:
# decisions made without their measurement. It passes c1 pool on its own claim that there is no
# pool (missing as absent), which does not count.
# photo-depth: wall 24, c1-gas 12, c1-facing 14.4, c2-gas 7.44, c2-facing 4.8, c2-route 13.2
# mesh-scaled: wall 1.2, c1-facing 1.2, c1-route 6, c2-gas 0.24, c2-facing 1.2
EXPECTED_RUNS = {
    "ar-taps": {
        "scored": "6",
        "median_abs_error_in": "3.48",  # (3.0 + 3.96) / 2
        "max_abs_error_in": "4.80",  # the 12 in scale-reference error is excluded
        "within_reported": "3",
        "with_reported_uncertainty": "6",
        "missing_unsupported": "0",
        "missing_failed": "1",
        "false_absent": "0",
        "phantom": "0",
        "absent_agreed": "1",
        "not_surveyed": "1",
        "judged": "7",
        "agrees": "4",
        "unsafe_passes": "1",  # c2 facing_gap, where the survey fails
        "missed_reviews": "2",  # c2 gas (borderline) and c2 route (review)
        "over_cautious": "0",
        "false_rejections": "0",
        "decided_without_measurement": "2",  # c2 facing_gap pass, c2 pool fail
        "abstentions_justified": "0",
        "abstentions_avoidable": "0",
        "could_flip": "2",
        "capture_s": "420.0",
        "processing_s": "12.5",
    },
    "photo-depth": {
        "scored": "6",
        "median_abs_error_in": "12.60",  # (12 + 13.2) / 2
        "max_abs_error_in": "24.00",
        "within_reported": "5",
        "with_reported_uncertainty": "6",
        "missing_unsupported": "1",
        "missing_failed": "0",
        "false_absent": "0",
        "phantom": "1",
        "absent_agreed": "0",
        "not_surveyed": "1",
        "judged": "7",
        "agrees": "2",
        "unsafe_passes": "0",
        "missed_reviews": "0",
        "over_cautious": "2",  # c1 facing_gap fail, c1 route unsure
        "false_rejections": "1",  # c1 facing_gap
        "decided_without_measurement": "0",  # its unsupported distances got unsure
        "abstentions_justified": "2",
        "abstentions_avoidable": "1",
        "could_flip": "2",
        "capture_s": "420.0",
        "processing_s": "95.0",
    },
    "mesh-scaled": {
        "scored": "5",
        "median_abs_error_in": "1.20",
        "max_abs_error_in": "6.00",
        "within_reported": "4",
        "with_reported_uncertainty": "4",  # wall-length has no reported +-
        "missing_unsupported": "0",
        "missing_failed": "1",
        "false_absent": "1",
        "phantom": "0",
        "absent_agreed": "1",
        "not_surveyed": "1",
        "judged": "7",
        # No decisions: the decision counts are empty, not zero.
        "agrees": "",
        "unsafe_passes": "",
        "missed_reviews": "",
        "over_cautious": "",
        "false_rejections": "",
        "decided_without_measurement": "",
        "abstentions_justified": "",
        "abstentions_avoidable": "",
        "could_flip": "0",
        "capture_s": "420.0",
        "processing_s": "",
    },
}


def test_run_summaries(scored):
    _, table = scored
    runs = by_key(table("runs.csv"), "pipeline")
    assert list(runs) == [(name,) for name in RUNS]
    for name, expected in EXPECTED_RUNS.items():
        row = runs[(name,)]
        assert row["measurements"] == "9"
        assert row["checks"] == "8"
        assert {key: row[key] for key in expected} == expected, name


def test_measurement_rows(scored):
    _, table = scored
    rows = by_key(table("measurements.csv"), "pipeline", "measurement")
    assert len(rows) == 30  # 3 runs x 10 survey measurements, scale reference included

    scale = rows[("ar-taps", "scale")]
    assert (scale["status"], scale["run_ft"], scale["signed_error_in"]) == (
        "scale_reference",
        "6.000",
        "",
    )
    assert rows[("photo-depth", "scale")]["run_ft"] == ""  # left out of that run

    facing = rows[("ar-taps", "c1-facing")]
    assert (facing["signed_error_in"], facing["abs_error_in"]) == ("-4.80", "4.80")
    assert facing["truth_within_reported"] == "false"

    edge = rows[("mesh-scaled", "c1-route")]  # 0.5 ft error, 0.5 ft reported
    assert (edge["signed_error_in"], edge["truth_within_reported"]) == ("-6.00", "true")

    assert rows[("mesh-scaled", "wall-length")]["truth_within_reported"] == ""
    assert rows[("mesh-scaled", "c1-gas")]["status"] == "false_absent"
    assert rows[("photo-depth", "c1-pool")]["status"] == "phantom"
    assert rows[("ar-taps", "c2-facing")]["status"] == "missing_failed"
    assert rows[("ar-taps", "c2-pool")]["status"] == "not_surveyed"


def test_check_rows(scored):
    _, table = scored
    rows = by_key(table("checks.csv"), "pipeline", "candidate", "check")
    assert len(rows) == 24

    borderline = rows[("ar-taps", "c2", "gas")]
    assert borderline["truth_outcome"] == "borderline"
    assert borderline["margin_ft"] == "0.020"
    assert borderline["error_to_margin"] == "11.00"  # 0.33 / max(0.02, 0.03)
    assert (borderline["could_flip"], borderline["agrees"]) == ("true", "false")
    assert (borderline["unsafe_pass"], borderline["missed_review"]) == ("false", "true")

    unsafe = rows[("ar-taps", "c2", "facing_gap")]  # passed with no value; the survey fails
    assert (unsafe["truth_outcome"], unsafe["run_outcome"]) == ("fail", "pass")
    assert (unsafe["unsafe_pass"], unsafe["missed_review"]) == ("true", "false")
    assert unsafe["decided_without_measurement"] == "true"

    unknown_blind = rows[("ar-taps", "c2", "pool")]  # failed with no value; survey unknown
    assert (unknown_blind["truth_outcome"], unknown_blind["agrees"]) == ("unknown", "")
    assert unknown_blind["decided_without_measurement"] == "true"

    absent_claim = rows[("ar-taps", "c1", "pool")]  # passed on its claim that there is no pool
    assert absent_claim["decided_without_measurement"] == "false"
    assert rows[("photo-depth", "c1", "route")]["decided_without_measurement"] == "false"
    assert rows[("mesh-scaled", "c2", "route")]["decided_without_measurement"] == ""

    route = rows[("ar-taps", "c2", "route")]
    assert route["truth_outcome"] == "review"
    assert (route["margin_ft"], route["review_margin_ft"]) == ("0.100", "-4.900")
    assert route["error_to_margin"] == "4.00"  # 0.4 / max(min(0.1, 4.9), 0.05)
    assert (route["missed_review"], route["unsafe_pass"], route["agrees"]) == (
        "true",
        "false",
        "false",
    )

    in_band = rows[("photo-depth", "c2", "route")]  # fail where the survey is review
    assert (in_band["false_rejection"], in_band["over_caution"], in_band["agrees"]) == (
        "false",
        "false",
        "false",
    )
    assert in_band["error_to_margin"] == "11.00"  # 1.1 / 0.1

    rejection = rows[("photo-depth", "c1", "facing_gap")]
    assert (rejection["false_rejection"], rejection["over_caution"]) == ("true", "true")

    unsupported = rows[("photo-depth", "c1", "route")]
    assert (unsupported["abstention"], unsupported["over_caution"]) == ("justified", "true")
    assert rows[("photo-depth", "c2", "facing_gap")]["abstention"] == "avoidable"
    assert rows[("mesh-scaled", "c1", "route")]["error_to_margin"] == "0.17"  # 0.5 / 3

    unknown = rows[("photo-depth", "c2", "pool")]
    assert (unknown["truth_outcome"], unknown["run_outcome"], unknown["agrees"]) == (
        "unknown",
        "unsure",
        "",
    )
    assert rows[("mesh-scaled", "c2", "gas")]["error_to_margin"] == "0.67"  # 0.02 / 0.03


def test_markdown_summary(scored):
    (stdout, stderr), _ = scored
    assert stdout.startswith("# Capture pipeline scores\n")
    assert "left out of every error figure" in stdout
    assert "Scale reference `scale` (5.000 ft), excluded." in stdout
    assert "| `ar-taps` | ar_poses | 6/9 | 3.48 | 4.80 | 3/6 | 0 | 1 | 0 | 0 | 1 | 1 |" in stdout
    assert (
        "| `mesh-scaled` | no decisions | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |" in stdout
    )
    assert "| `ar-taps` | 4/7 | 1 | 2 | 0 | 0 | 2 | 0 | 0 | 2 |" in stdout
    assert "| `mesh-scaled` | 420.0 | not recorded |" in stdout
    unsafe, rest = stdout.split("### Unsafe passes")[1].split("### Missed reviews")
    missed, blind = rest.split("### Decided without its measurement")
    blind = blind.split("\n\nThese are paired results")[0]
    assert blind.strip().splitlines() == [
        "- `ar-taps` reported pass for `facing_gap` at `c2` with measurement `c2-facing` missing "
        "(failed); the survey is fail at 2.500 ± 0.020 ft against `facing_gap_ft` at_least "
        "3.000 ft. Capture `synthetic-01-walk-1`.",
        "- `ar-taps` reported fail for `pool` at `c2` with measurement `c2-pool` missing "
        "(failed); the survey is unknown (not measured) against `pool_clearance_ft` at_least "
        "5.000 ft. Capture `synthetic-01-walk-1`.",
    ]
    assert unsafe.strip() == (
        "- `ar-taps` passed `facing_gap` at `c2`; the survey is fail at 2.500 ± 0.020 ft "
        "against `facing_gap_ft` at_least 3.000 ft (run measured no value). "
        "Capture `synthetic-01-walk-1`."
    )
    assert (
        "- `ar-taps` passed `gas` at `c2`; the survey is borderline at 3.020 ± 0.030 ft against "
        "`gas_clearance_ft` at_least 3.000 ft (run measured 3.350 ft)."
    ) in missed
    assert (
        "- `ar-taps` passed `route` at `c2`; the survey is review at 19.900 ± 0.050 ft against "
        "`review_route_ft` 15.000 ft and `max_route_ft` at_most 20.000 ft (run measured 19.500 ft)."
    ) in missed
    assert "case series" in stdout
    assert "wrote" in stderr and "runs.csv" in stderr


def test_bad_input_exits_2_with_the_reason(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    code = main(
        [
            "--rules",
            str(FIXTURES / "rules.json"),
            "--truth",
            str(FIXTURES / "truth" / "synthetic-01.json"),
            "--results",
            str(tmp_path / "missing.json"),
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 2
    assert "cannot read" in capsys.readouterr().err
    assert not (tmp_path / "runs.csv").exists()
