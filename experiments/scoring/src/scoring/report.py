"""Turn run scores into a markdown summary and three CSV tables.

CSV cells hold the figure, or stay empty when it does not apply (a missing distance has no error).
Booleans are written true or false. Decimals are rounded half up for display only; every
comparison was made on the exact values.
"""

import csv
import re
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from pathlib import Path

from scoring.inputs import Study
from scoring.metrics import AtThreshold, CheckScore, MeasurementScore, RunScore

MEASUREMENT_COLUMNS = (
    "house",
    "capture",
    "pipeline",
    "measurement",
    "candidate",
    "from",
    "to",
    "survey_status",
    "survey_ft",
    "survey_plus_minus_ft",
    "run_ft",
    "run_plus_minus_ft",
    "run_missing",
    "status",
    "signed_error_in",
    "abs_error_in",
    "truth_within_reported",
)

CHECK_COLUMNS = (
    "house",
    "capture",
    "pipeline",
    "candidate",
    "check",
    "measurement",
    "threshold",
    "threshold_ft",
    "review_threshold",
    "review_threshold_ft",
    "pass_when",
    "survey_ft",
    "survey_plus_minus_ft",
    "margin_ft",
    "review_margin_ft",
    "truth_outcome",
    "run_ft",
    "abs_error_in",
    "error_to_margin",
    "could_flip",
    "run_outcome",
    "expected_outcome",
    "agrees",
    "unsafe_pass",
    "missed_review",
    "over_caution",
    "false_rejection",
    "decided_without_measurement",
    "abstention",
)

RUN_COLUMNS = (
    "house",
    "capture",
    "pipeline",
    "scale_source",
    "capture_s",
    "processing_s",
    "measurements",
    "scored",
    "median_abs_error_in",
    "max_abs_error_in",
    "within_reported",
    "with_reported_uncertainty",
    "missing_unsupported",
    "missing_failed",
    "false_absent",
    "phantom",
    "absent_agreed",
    "not_surveyed",
    "checks",
    "judged",
    "agrees",
    "unsafe_passes",
    "missed_reviews",
    "over_cautious",
    "false_rejections",
    "decided_without_measurement",
    "abstentions_justified",
    "abstentions_avoidable",
    "could_flip",
)


def fixed(value: Decimal | None, places: int) -> str:
    """Round half up to `places` decimals. Never prints -0.00 or scientific notation."""
    if value is None:
        return ""
    rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = abs(rounded)
    return format(rounded, "f")


def feet(value: Decimal | None) -> str:
    return fixed(value, 3)


def inches(value: Decimal | None) -> str:
    return fixed(value, 2)


def ratio(value: Decimal | AtThreshold | None) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else fixed(value, 2)


def flag(value: bool | None) -> str:
    return "" if value is None else str(value).lower()


def seconds(value: Decimal | None) -> str:
    return fixed(value, 1)


def measurement_row(run: RunScore, score: MeasurementScore) -> dict[str, str]:
    survey, reported = score.survey, score.reported
    return {
        "house": run.truth.house,
        "capture": run.results.capture,
        "pipeline": run.results.pipeline,
        "measurement": survey.id,
        "candidate": survey.candidate or "",
        "from": survey.start,
        "to": survey.end,
        "survey_status": survey.status,
        "survey_ft": feet(survey.value_ft),
        "survey_plus_minus_ft": feet(survey.plus_minus_ft),
        "run_ft": feet(reported.value_ft) if reported else "",
        "run_plus_minus_ft": feet(reported.plus_minus_ft) if reported else "",
        "run_missing": (reported.missing or "") if reported else "",
        "status": score.status,
        "signed_error_in": inches(score.signed_error_in),
        "abs_error_in": inches(score.abs_error_in),
        "truth_within_reported": flag(score.truth_within_reported),
    }


def check_row(run: RunScore, score: CheckScore) -> dict[str, str]:
    reported = score.measurement.reported
    return {
        "house": run.truth.house,
        "capture": run.results.capture,
        "pipeline": run.results.pipeline,
        "candidate": score.check.candidate,
        "check": score.check.check,
        "measurement": score.check.measurement,
        "threshold": score.threshold.name,
        "threshold_ft": feet(score.threshold.value_ft),
        "review_threshold": score.review.name if score.review else "",
        "review_threshold_ft": feet(score.review.value_ft) if score.review else "",
        "pass_when": score.threshold.pass_when,
        "survey_ft": feet(score.survey.value_ft),
        "survey_plus_minus_ft": feet(score.survey.plus_minus_ft),
        "margin_ft": feet(score.margin_ft),
        "review_margin_ft": feet(score.review_margin_ft),
        "truth_outcome": score.truth,
        "run_ft": feet(reported.value_ft) if reported else "",
        "abs_error_in": inches(score.measurement.abs_error_in),
        "error_to_margin": ratio(score.error_to_margin),
        "could_flip": flag(score.could_flip),
        "run_outcome": score.reported or "",
        "expected_outcome": score.expected or "",
        "agrees": flag(score.agrees),
        "unsafe_pass": flag(score.unsafe_pass),
        "missed_review": flag(score.missed_review),
        "over_caution": flag(score.over_caution),
        "false_rejection": flag(score.false_rejection),
        "decided_without_measurement": flag(score.decided_without_measurement),
        "abstention": score.abstention or "",
    }


def run_row(run: RunScore) -> dict[str, str]:
    within, with_uncertainty = run.within_reported
    decides = run.makes_decisions

    def decision(count: int) -> str:
        return str(count) if decides else ""

    return {
        "house": run.truth.house,
        "capture": run.results.capture,
        "pipeline": run.results.pipeline,
        "scale_source": run.results.scale_source,
        "capture_s": seconds(run.results.capture_seconds),
        "processing_s": seconds(run.results.processing_seconds),
        "measurements": str(run.denominator),
        "scored": str(run.count("scored")),
        "median_abs_error_in": inches(run.median_abs_error_in),
        "max_abs_error_in": inches(run.max_abs_error_in),
        "within_reported": str(within),
        "with_reported_uncertainty": str(with_uncertainty),
        "missing_unsupported": str(run.count("missing_unsupported")),
        "missing_failed": str(run.count("missing_failed")),
        "false_absent": str(run.count("false_absent")),
        "phantom": str(run.count("phantom")),
        "absent_agreed": str(run.count("absent_agreed")),
        "not_surveyed": str(run.count("not_surveyed")),
        "checks": str(len(run.checks)),
        "judged": str(run.judged),
        "agrees": decision(run.agreements),
        "unsafe_passes": decision(run.unsafe_passes),
        "missed_reviews": decision(run.missed_reviews),
        "over_cautious": decision(run.over_cautious),
        "false_rejections": decision(run.false_rejections),
        "decided_without_measurement": decision(run.decided_without_measurement),
        "abstentions_justified": decision(run.abstentions("justified")),
        "abstentions_avoidable": decision(run.abstentions("avoidable")),
        "could_flip": str(run.could_flip),
    }


def _write(path: Path, columns: tuple[str, ...], rows: Iterable[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


# The files write_csvs creates in the output directory, in the order it returns them.
CSV_NAMES = ("measurements.csv", "checks.csv", "runs.csv")


def csv_paths(out_dir: Path) -> list[Path]:
    return [out_dir / name for name in CSV_NAMES]


def write_csvs(runs: list[RunScore], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = csv_paths(out_dir)
    measurement_rows = (measurement_row(r, s) for r in runs for s in r.measurements)
    _write(paths[0], MEASUREMENT_COLUMNS, measurement_rows)
    _write(paths[1], CHECK_COLUMNS, (check_row(r, s) for r in runs for s in r.checks))
    _write(paths[2], RUN_COLUMNS, (run_row(r) for r in runs))
    return paths


# Identifiers in the inputs may be any non-empty string, so every one is escaped where it lands in
# the summary: a pipe would split a table cell, a line break would end a row, heading or list item,
# and a backtick would close a code span early.
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>|#])")


def _one_line(value: str) -> str:
    return _LINE_BREAK.sub(" ", value)


def _code(value: str) -> str:
    """`value` as one inline code span. The fence is one backtick longer than any backtick run
    inside, and a space pads a value that starts or ends with a backtick, or that starts and ends
    with a space (CommonMark strips one such space from each side)."""
    text = _one_line(value)
    fence = "`" * (max((len(run) for run in re.findall("`+", text)), default=0) + 1)
    edge_tick = text.startswith("`") or text.endswith("`")
    edge_spaces = text.startswith(" ") and text.endswith(" ") and text.strip() != ""
    pad = " " if edge_tick or edge_spaces else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _text(value: str) -> str:
    """`value` as plain inline text, on one line, with Markdown punctuation escaped."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", _one_line(value))


def _cell(value: str) -> str:
    # GitHub splits a table row at every unescaped pipe, even inside a code span.
    return value.replace("\\", "\\\\").replace("|", "\\|")


def _table_code(value: str) -> str:
    # Code spans preserve backslashes. HTML keeps the displayed identifier intact while
    # preventing a backslash before a pipe from changing the table's column boundaries.
    if "\\" in value:
        return "<code>" + escape(_one_line(value)).replace("|", "&#124;") + "</code>"
    return _code(value)


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(cell) or "n/a" for cell in row) + " |" for row in rows]
    return lines


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def markdown(study: Study, runs: list[RunScore]) -> str:
    rules = study.rules
    houses = study.houses
    spots = sum(len(house.truth.candidates) for house in houses)
    lines = [
        "# Capture pipeline scores",
        "",
        f"Rules: {_code(rules.path.name)} ({_text(rules.name)}), sha256 {_code(rules.sha256)}.",
        f"{_count(len(houses), 'house')}, {_count(spots, 'candidate spot')}, "
        f"{_count(len(runs), 'pipeline run')}.",
        "",
        "Each house's declared scale reference is left out of every error figure: a run that was "
        "given it can match it exactly, so its error says nothing about accuracy.",
    ]
    for house in houses:
        truth = house.truth
        house_runs = [run for run in runs if run.truth is truth]
        reference = truth.measurements[truth.scale_reference]
        lines += [
            "",
            f"## House {_text(truth.house)}",
            "",
            f"Captures: {', '.join(map(_text, truth.captures))}. "
            f"Candidates: {', '.join(map(_text, truth.candidates))}. "
            f"Scale reference {_code(reference.id)} ({feet(reference.value_ft)} ft), excluded.",
        ]
        if not house_runs:
            lines += ["", "No pipeline runs for this house."]
            continue
        lines += _distances(house_runs) + _checks(house_runs) + _timing(house_runs)
        lines += _named_lists(house_runs)
    lines += [
        "",
        "These are paired results at the listed spots, a case series. They are not an accuracy "
        "rate for other homes, validated error bars, or evidence of safety.",
    ]
    return "\n".join(lines) + "\n"


def _distances(runs: list[RunScore]) -> list[str]:
    denominator = runs[0].denominator
    rows = []
    for run in runs:
        within, with_uncertainty = run.within_reported
        rows.append(
            [
                _table_code(run.results.pipeline),
                run.results.scale_source,
                f"{run.count('scored')}/{denominator}",
                inches(run.median_abs_error_in),
                inches(run.max_abs_error_in),
                f"{within}/{with_uncertainty}" if with_uncertainty else "",
                str(run.count("missing_unsupported")),
                str(run.count("missing_failed")),
                str(run.count("false_absent")),
                str(run.count("phantom")),
                str(run.count("absent_agreed")),
                str(run.count("not_surveyed")),
                _table_code(run.results.capture),
            ]
        )
    header = [
        "Pipeline",
        "Scale source",
        "Scored",
        "Median error (in)",
        "Max error (in)",
        "Survey inside run's ±",
        "Unsupported",
        "Failed",
        "Wrongly absent",
        "Phantom",
        "Absent, agreed",
        "Not surveyed",
        "Capture",
    ]
    return [
        "",
        "### Distances",
        "",
        f"{denominator} distances besides the scale reference; each row's counts add up to it. "
        "Errors are absolute, in inches, over the distances both the survey and the run "
        "measured. Wrongly absent: the run says the feature is not there, but the survey "
        "measured it. Phantom: the run measured a feature the survey found absent.",
        "",
        *_table(header, rows),
    ]


def _checks(runs: list[RunScore]) -> list[str]:
    total = len(runs[0].checks)
    judged = runs[0].judged
    rows = []
    for run in runs:
        if run.makes_decisions:
            decided = [
                f"{run.agreements}/{judged}",
                str(run.unsafe_passes),
                str(run.missed_reviews),
                str(run.over_cautious),
                str(run.false_rejections),
                str(run.decided_without_measurement),
                str(run.abstentions("justified")),
                str(run.abstentions("avoidable")),
            ]
        else:
            decided = ["no decisions", "", "", "", "", "", "", ""]
        rows.append(
            [
                _table_code(run.results.pipeline),
                *decided,
                str(run.could_flip),
                _table_code(run.results.capture),
            ]
        )
    header = [
        "Pipeline",
        "Agree",
        "Unsafe passes",
        "Missed reviews",
        "Over-cautious",
        "False rejections",
        "Decided without its measurement",
        "Unsure, justified",
        "Unsure, avoidable",
        "Error could flip",
        "Capture",
    ]
    return [
        "",
        "### Checks",
        "",
        f"{total} checks, {judged} with a survey outcome. The survey passes a check when it "
        "clears the pass line by more than its ± and fails it when it misses the fail line by "
        "more than its ±. Anything between, including a value exactly on a line, is borderline "
        "(one threshold) or review (a band such as review_route_ft to max_route_ft), where the "
        "right answer is unsure. Unsafe pass: a pass where the survey fails. Missed review: a "
        "pass where the survey is borderline or review. Over-cautious: unsure or fail where the "
        "survey passes; false rejections are the fails among them. Decided without its "
        "measurement: a pass or fail with the run's measurement missing as failed or "
        "unsupported; a claimed absence also cannot support an at_most decision or an at_least "
        "fail. It is also scored as usual. An unsure is justified when the survey is borderline "
        "or review, or the run's measurement failed or is unsupported. The error could flip a "
        "check when it is at least as large as both the survey's "
        "distance to the nearest threshold and its ±.",
        "",
        *_table(header, rows),
    ]


def _timing(runs: list[RunScore]) -> list[str]:
    rows = [
        [
            _table_code(run.results.pipeline),
            seconds(run.results.capture_seconds) or "not recorded",
            seconds(run.results.processing_seconds) or "not recorded",
            _table_code(run.results.capture),
        ]
        for run in runs
    ]
    return [
        "",
        "### Timing",
        "",
        *_table(["Pipeline", "Capture (s)", "Processing (s)", "Capture"], rows),
    ]


def _survey_text(score: CheckScore) -> str:
    """The survey outcome and the lines it was judged against, for the named lists."""
    survey, threshold, review = score.survey, score.threshold, score.review
    limits = f"{_code(threshold.name)} {threshold.pass_when} {feet(threshold.value_ft)} ft"
    if review is not None:
        limits = f"{_code(review.name)} {feet(review.value_ft)} ft and {limits}"
    if survey.value_ft is None:
        return f"the survey is {score.truth} ({survey.status.replace('_', ' ')}) against {limits}"
    return (
        f"the survey is {score.truth} at {feet(survey.value_ft)} ± "
        f"{feet(survey.plus_minus_ft)} ft against {limits}"
    )


def _wrong_pass(run: RunScore, score: CheckScore) -> str:
    reported = score.measurement.reported
    has_value = reported is not None and reported.value_ft is not None
    run_value = f"{feet(reported.value_ft)} ft" if has_value else "no value"
    return (
        f"- {_code(run.results.pipeline)} passed {_code(score.check.check)} at "
        f"{_code(score.check.candidate)}; {_survey_text(score)} (run measured {run_value}). "
        f"Capture {_code(run.results.capture)}."
    )


def _decision_without_measurement(run: RunScore, score: CheckScore) -> str:
    reported = score.measurement.reported
    assert reported is not None and reported.missing is not None
    return (
        f"- {_code(run.results.pipeline)} reported {score.reported} for "
        f"{_code(score.check.check)} at {_code(score.check.candidate)} with measurement "
        f"{_code(score.check.measurement)} missing "
        f"({reported.missing}); {_survey_text(score)}. Capture {_code(run.results.capture)}."
    )


def _named_lists(runs: list[RunScore]) -> list[str]:
    """Every unsafe pass, missed review and decision made without its measurement, by name."""
    lines = []
    for title, empty, picked, line in (
        ("Unsafe passes", "No unsafe passes.", lambda s: s.unsafe_pass, _wrong_pass),
        ("Missed reviews", "No missed reviews.", lambda s: s.missed_review, _wrong_pass),
        (
            "Decided without its measurement",
            "No such decisions.",
            lambda s: s.decided_without_measurement,
            _decision_without_measurement,
        ),
    ):
        found = [line(run, score) for run in runs for score in run.checks if picked(score)]
        lines += ["", f"### {title}", "", *found] if found else ["", empty]
    return lines
