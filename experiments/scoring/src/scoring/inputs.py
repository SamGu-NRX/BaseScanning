"""Load and validate the three input files: the rules, a house's tape survey, and a pipeline run.

Any problem raises InputError naming the file, the field and what was wrong. A typo in a hand-typed
survey has to stop the run, because a silently skipped row changes the score without anyone seeing.

Numbers are parsed as Decimal, not float. Checks compare survey values against thresholds, and an
exact tie has to land on the same side every time: in floats 3.1 - 3.0 is 0.10000000000000009, which
would pass a 3.1 +- 0.1 ft clearance against a 3 ft rule that is exactly borderline.
"""

import hashlib
import json
import os
import re
from collections.abc import Container, Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

FORMAT = 1
UNIT = "ft"

PassWhen = Literal["at_least", "at_most"]
SurveyStatus = Literal["measured", "absent", "not_measured"]
MissingReason = Literal["unsupported", "failed", "absent"]
Outcome = Literal["pass", "unsure", "fail"]
ScaleSource = Literal["native_metric", "scale_reference", "ar_poses"]

PASS_WHEN: tuple[PassWhen, ...] = ("at_least", "at_most")
SURVEY_STATUSES: tuple[SurveyStatus, ...] = ("measured", "absent", "not_measured")
MISSING_REASONS: tuple[MissingReason, ...] = ("unsupported", "failed", "absent")
OUTCOMES: tuple[Outcome, ...] = ("pass", "unsure", "fail")
SCALE_SOURCES: tuple[ScaleSource, ...] = ("native_metric", "scale_reference", "ar_poses")

# Threshold names follow rules.yaml, where every length parameter ends in _ft.
THRESHOLD_NAME = re.compile(r"[a-z][a-z0-9_]*_ft")
SHA256 = re.compile(r"[0-9a-f]{64}")


class InputError(ValueError):
    """An input file is malformed or inconsistent with the other inputs."""


@dataclass(frozen=True)
class Threshold:
    """One named threshold from the rules file, in feet.

    `pass_when` says how a measurement passes: `at_least` for a clearance, `at_most` for a limit
    such as a route length. `source` records where the value came from.
    """

    name: str
    value_ft: Decimal
    pass_when: PassWhen
    source: str


@dataclass(frozen=True)
class Rules:
    """The parsed rules file: a name and the thresholds every check is scored against.

    `sha256` is the hash of the file's exact bytes. A results file must carry the same hash in
    `rules_sha256`; `load_study` rejects a run made under different rules.
    """

    path: Path
    sha256: str
    name: str
    thresholds: dict[str, Threshold]


@dataclass(frozen=True)
class Candidate:
    """One spot on the house that the survey covers.

    `marker` is the physical mark a surveyor left, and `location` says where it is, as tape
    offsets from a permanent corner.
    """

    id: str
    marker: str
    location: str


@dataclass(frozen=True)
class SurveyMeasurement:
    """One distance the survey describes, between the endpoints `start` and `end`.

    The JSON fields are `from` and `to`; Python renames them because `from` is a keyword. Only a
    `measured` entry carries `value_ft` and `plus_minus_ft`. `candidate` is null for a house-level
    distance such as a wall length, and `measured_by` names every surveyor who took it.
    """

    id: str
    candidate: str | None
    start: str
    end: str
    status: SurveyStatus
    value_ft: Decimal | None
    plus_minus_ft: Decimal | None
    method: str
    measured_by: tuple[str, ...]


@dataclass(frozen=True)
class Check:
    """One question the survey answers about a candidate, decided by one measurement against one
    threshold from the rules file.
    """

    candidate: str
    check: str
    measurement: str
    # The fail line. With review_threshold set, values between the two lines are for review.
    threshold: str
    review_threshold: str | None = None


@dataclass(frozen=True)
class Truth:
    """The parsed survey of one house.

    `captures` names the recordings the survey applies to, and `scale_reference` names the one
    measurement a pipeline may use to set scale, which is never scored.
    """

    path: Path
    house: str
    captures: tuple[str, ...]
    scale_reference: str
    candidates: dict[str, Candidate]
    measurements: dict[str, SurveyMeasurement]
    checks: tuple[Check, ...]


@dataclass(frozen=True)
class PipelineMeasurement:
    """One distance as a pipeline run reported it.

    Either `value_ft` holds the run's value with `plus_minus_ft` as its uncertainty, or the value
    is null and `missing` says why the run has none.
    """

    id: str
    value_ft: Decimal | None
    plus_minus_ft: Decimal | None
    missing: MissingReason | None


@dataclass(frozen=True)
class Results:
    """One pipeline run on one recording, as its results file reports it.

    `rules_sha256` is the hash of the rules file the run says it used; `load_study` rejects a run
    that does not match the study's rules.
    """

    path: Path
    pipeline: str
    capture: str
    rules_sha256: str
    scale_source: ScaleSource
    measurements: dict[str, PipelineMeasurement]
    # None means the run makes no pass/unsure/fail decisions (a distances-only row).
    outcomes: dict[tuple[str, str], Outcome] | None
    capture_seconds: Decimal | None
    processing_seconds: Decimal | None


@dataclass(frozen=True)
class House:
    """One house's survey with every run captured from one of its recordings.

    A run pairs with the survey that lists its capture id.
    """

    truth: Truth
    runs: tuple[Results, ...]


@dataclass(frozen=True)
class Study:
    """The rules and every house's survey with its runs, all loaded and validated."""

    rules: Rules
    houses: tuple[House, ...]


def _describe(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"the boolean {str(value).lower()}"
    if isinstance(value, str):
        return f"the string {value!r}"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "an object"
    return f"{value!r}"


class Fields:
    """One JSON object being validated. `path` locates it inside `file` for error messages."""

    def __init__(self, data: Any, file: Path, path: str, allowed: Iterable[str]):
        self.file = file
        self.path = path
        if not isinstance(data, dict):
            raise self.error(f"expected an object, got {_describe(data)}")
        allowed = set(allowed)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise self.error(
                f"unknown field {', '.join(map(repr, unknown))}; "
                f"allowed fields are {', '.join(sorted(allowed))}"
            )
        self.data: dict[str, Any] = data

    def error(self, message: str, key: str | None = None) -> InputError:
        """Build an InputError that names the file and this object's location in it.

        The caller raises it. `key` extends the location with the field's name.
        """
        where = ".".join(part for part in (self.path, key) if part)
        prefix = f"{self.file}: {where}" if where else str(self.file)
        return InputError(f"{prefix}: {message}")

    def has(self, key: str) -> bool:
        """True when the key is present, even if its value is null."""
        return key in self.data

    def raw(self, key: str) -> Any:
        """The value at `key`. The key must be present even when its value is null.

        A field that may be null is read with `optional_text` or `optional_length`.
        """
        if key not in self.data:
            raise self.error(f"missing required field {key!r}")
        return self.data[key]

    def text(self, key: str) -> str:
        """A non-empty string; a whitespace-only value is rejected."""
        value = self.raw(key)
        if not isinstance(value, str) or not value.strip():
            raise self.error(f"expected a non-empty string, got {_describe(value)}", key)
        return value

    def optional_text(self, key: str) -> str | None:
        """Like `text`, but an explicit null is allowed. The key itself is still required."""
        return None if self.raw(key) is None else self.text(key)

    def length(self, key: str) -> Decimal:
        """A non-negative finite number: a distance, a tolerance or a duration."""
        value = self.raw(key)
        if isinstance(value, bool) or not isinstance(value, int | Decimal):
            raise self.error(f"expected a number, got {_describe(value)}", key)
        if value < 0:
            raise self.error(f"must not be negative, got {value}", key)
        # Keep derived inches and error-to-margin ratios within Decimal's 28-digit
        # reporting precision, including a 1e-12 ft distance to a threshold.
        number = Decimal(value)
        if number > Decimal("1000000000") or number.as_tuple().exponent < -12:
            raise self.error("must be at most 1000000000 with at most 12 decimal places", key)
        return number

    def optional_length(self, key: str) -> Decimal | None:
        """Like length, but an explicit null is allowed. The key itself is still required."""
        return None if self.raw(key) is None else self.length(key)

    def choice[T: str](self, key: str, choices: tuple[T, ...]) -> T:
        """The value at `key`, which must be one of `choices`; the error names every valid one."""
        value = self.raw(key)
        if value not in choices:
            raise self.error(f"expected one of {', '.join(choices)}, got {_describe(value)}", key)
        return value

    def items(self, key: str) -> list[Any]:
        """A non-empty list whose entries go unchecked.

        The caller validates each one, usually by wrapping it with `child`.
        """
        value = self.raw(key)
        if not isinstance(value, list) or not value:
            raise self.error(f"expected a non-empty list, got {_describe(value)}", key)
        return value

    def child(self, value: Any, path: str, allowed: Iterable[str]) -> "Fields":
        """A new `Fields` for a nested value, such as one entry of a list.

        Errors from the child name the same file and the deeper path.
        """
        return Fields(value, self.file, path, allowed)

    def text_list(self, key: str) -> tuple[str, ...]:
        """A non-empty list of non-empty strings, with no value listed twice, as a tuple."""
        values = self.items(key)
        for index, value in enumerate(values):
            if not isinstance(value, str) or not value.strip():
                raise self.error(
                    f"expected a non-empty string, got {_describe(value)}", f"{key}[{index}]"
                )
        if len(set(values)) != len(values):
            raise self.error("lists the same value twice", key)
        return tuple(values)

    def header(self) -> None:
        """Every file starts with the format version and the length unit."""
        version = self.raw("format")
        if type(version) is not int or version != FORMAT:
            raise self.error(
                f"this scorer reads format {FORMAT}, got {_describe(version)}", "format"
            )
        if self.raw("unit") != UNIT:
            raise self.error(
                f'every length must be in feet ("{UNIT}"), got {_describe(self.raw("unit"))}',
                "unit",
            )


def refuse_output_over_inputs(outputs: Iterable[Path], inputs: Iterable[tuple[str, Path]]) -> None:
    """Stop before any write when an output file is one of the inputs, whether by the same path,
    another spelling of it, a symlink or a hard link."""
    inputs = list(inputs)
    for out_path in outputs:
        out_resolved = out_path.resolve()
        for role, path in inputs:
            same = out_resolved == path.resolve()
            if not same and out_path.exists() and path.exists():
                same = os.path.samefile(out_path, path)
            if same:
                raise InputError(
                    f"--out {out_path} is the {role} {path}; writing there would overwrite an "
                    "input. Choose another output path"
                )


def read_json(path: Path) -> tuple[Any, bytes]:
    """Read and parse a JSON file, returning its parsed value and its exact bytes.

    `load_rules` hashes the bytes for `Rules.sha256`. An unreadable or invalid file raises
    InputError.
    """
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise InputError(f"{path}: cannot read ({error.strerror})") from None
    return parse_json(raw, str(path)), raw


def parse_json(raw: bytes, path: str) -> Any:
    """Parse JSON with every float as an exact Decimal, rejecting duplicate keys and NaN.

    `path` names the source in error messages, which may be a member inside a zip.
    """

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        for key, value in pairs:
            if key in seen:
                raise InputError(
                    f"{path}: key {key!r} appears twice in one object; JSON would keep only the "
                    "last one, so the file is ambiguous"
                )
            seen[key] = value
        return seen

    def reject_constant(name: str) -> Any:
        raise InputError(f"{path}: {name} is not a measurement; write null for a missing value")

    try:
        return json.loads(
            raw,
            parse_float=Decimal,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except json.JSONDecodeError as error:
        raise InputError(
            f"{path}: not valid JSON ({error.msg} at line {error.lineno}, column {error.colno})"
        ) from None
    except UnicodeDecodeError:
        raise InputError(f"{path}: not UTF-8 text") from None


def _unique_id(fields: Fields, seen: Container[str], kind: str) -> str:
    value = fields.text("id")
    if value in seen:
        raise fields.error(f"{kind} {value!r} is listed twice", "id")
    return value


def load_rules(path: Path) -> Rules:
    """Load and validate the rules file.

    A threshold's key is its rules.yaml parameter name: lower_snake_case, ending in _ft because
    every value is in feet. `Rules.sha256` hashes the exact bytes read, so `load_study` can
    reject a run made under different rules.
    """
    data, raw = read_json(path)
    top = Fields(data, path, "", {"format", "unit", "name", "thresholds"})
    top.header()
    name = top.text("name")
    table = top.raw("thresholds")
    if not isinstance(table, dict) or not table:
        raise top.error("expected an object with at least one threshold", "thresholds")
    thresholds: dict[str, Threshold] = {}
    for key, entry in table.items():
        fields = top.child(entry, f"thresholds.{key}", {"value_ft", "pass_when", "source"})
        if not THRESHOLD_NAME.fullmatch(key):
            raise fields.error(
                "a threshold is named by its rules.yaml parameter: lower_snake_case, ending in "
                "_ft because every value is in feet"
            )
        thresholds[key] = Threshold(
            name=key,
            value_ft=fields.length("value_ft"),
            pass_when=fields.choice("pass_when", PASS_WHEN),
            source=fields.text("source"),
        )
    return Rules(path, hashlib.sha256(raw).hexdigest(), name, thresholds)


SURVEY_MEASUREMENT_FIELDS = (
    "id",
    "candidate",
    "from",
    "to",
    "status",
    "value_ft",
    "plus_minus_ft",
    "method",
    "measured_by",
)


def _load_survey_measurement(fields: Fields, seen: Container[str]) -> SurveyMeasurement:
    measurement_id = _unique_id(fields, seen, "measurement")
    fields.path += f" ({measurement_id})"
    status = fields.choice("status", SURVEY_STATUSES)
    value = plus_minus = None
    if status == "measured":
        value = fields.length("value_ft")
        plus_minus = fields.length("plus_minus_ft")
    else:
        for key in ("value_ft", "plus_minus_ft"):
            if fields.has(key):
                raise fields.error(
                    f'status {status!r} has no value; remove {key} or set status to "measured"',
                    key,
                )
    return SurveyMeasurement(
        id=measurement_id,
        candidate=fields.optional_text("candidate"),
        start=fields.text("from"),
        end=fields.text("to"),
        status=status,
        value_ft=value,
        plus_minus_ft=plus_minus,
        method=fields.text("method"),
        measured_by=fields.text_list("measured_by"),
    )


def load_truth(path: Path, rules: Rules) -> Truth:
    """Load and validate the survey of one house.

    Cross-references must hold: a measurement's candidate, a check's measurement and a check's
    threshold all have to exist. The scale reference must be measured and can decide no check,
    every spot needs the same checks with the same thresholds, and a review threshold must sit
    strictly on the passing side of its fail line.
    """
    data, _ = read_json(path)
    top = Fields(
        data,
        path,
        "",
        {
            "format",
            "unit",
            "house",
            "captures",
            "scale_reference",
            "candidates",
            "measurements",
            "checks",
        },
    )
    top.header()

    candidates: dict[str, Candidate] = {}
    for index, entry in enumerate(top.items("candidates")):
        fields = top.child(entry, f"candidates[{index}]", {"id", "marker", "location"})
        candidate_id = _unique_id(fields, candidates, "candidate")
        candidates[candidate_id] = Candidate(
            candidate_id, fields.text("marker"), fields.text("location")
        )

    measurements: dict[str, SurveyMeasurement] = {}
    for index, entry in enumerate(top.items("measurements")):
        fields = top.child(entry, f"measurements[{index}]", SURVEY_MEASUREMENT_FIELDS)
        measurement = _load_survey_measurement(fields, measurements)
        if measurement.candidate is not None and measurement.candidate not in candidates:
            raise fields.error(
                f"no candidate {measurement.candidate!r}; candidates are {', '.join(candidates)}",
                "candidate",
            )
        measurements[measurement.id] = measurement

    scale_reference = top.text("scale_reference")
    reference = measurements.get(scale_reference)
    if reference is None:
        raise top.error(f"{scale_reference!r} is not a measurement id", "scale_reference")
    if reference.status != "measured":
        raise top.error(
            f"{scale_reference!r} must be measured, but its status is {reference.status!r}",
            "scale_reference",
        )

    checks: list[Check] = []
    for index, entry in enumerate(top.items("checks")):
        fields = top.child(
            entry,
            f"checks[{index}]",
            {"candidate", "check", "measurement", "threshold", "review_threshold"},
        )
        check = Check(
            candidate=fields.text("candidate"),
            check=fields.text("check"),
            measurement=fields.text("measurement"),
            threshold=fields.text("threshold"),
            review_threshold=(
                fields.text("review_threshold") if fields.has("review_threshold") else None
            ),
        )
        if any((c.candidate, c.check) == (check.candidate, check.check) for c in checks):
            raise fields.error(f"check {check.check!r} at {check.candidate!r} is listed twice")
        _validate_check(fields, check, candidates, measurements, scale_reference, rules)
        checks.append(check)
    _require_same_checks_at_every_spot(top, candidates, checks)

    return Truth(
        path=path,
        house=top.text("house"),
        captures=top.text_list("captures"),
        scale_reference=scale_reference,
        candidates=candidates,
        measurements=measurements,
        checks=tuple(checks),
    )


def _require_same_checks_at_every_spot(
    top: Fields, candidates: dict[str, Candidate], checks: list[Check]
) -> None:
    """Every spot needs the same check names, each with the same thresholds.

    The protocol applies every distance to every spot, so the denominator is fixed. A survey that
    left a hard check out at one spot would otherwise score with a smaller denominator unnoticed.
    """
    names: dict[str, set[str]] = {candidate: set() for candidate in candidates}
    policies: dict[str, tuple[str, str | None]] = {}
    for check in checks:
        names[check.candidate].add(check.check)
        policy = (check.threshold, check.review_threshold)
        previous = policies.setdefault(check.check, policy)
        if policy != previous:
            raise top.error(
                f"check {check.check!r} at candidate {check.candidate!r} uses threshold "
                f"{check.threshold!r} and review_threshold {check.review_threshold!r}, "
                f"but other spots use {previous[0]!r} and {previous[1]!r}; "
                "every spot needs the same threshold mapping",
                "checks",
            )
    every = set().union(*names.values())
    for candidate, found in names.items():
        if found != every:
            raise top.error(
                f"candidate {candidate!r} has no {', '.join(sorted(every - found))} check; "
                "every spot needs the same checks. Survey the distance, or record it as "
                "absent or not_measured"
            )


def _validate_check(
    fields: Fields,
    check: Check,
    candidates: dict[str, Candidate],
    measurements: dict[str, SurveyMeasurement],
    scale_reference: str,
    rules: Rules,
) -> None:
    if check.candidate not in candidates:
        raise fields.error(f"no candidate {check.candidate!r}", "candidate")
    measurement = measurements.get(check.measurement)
    if measurement is None:
        raise fields.error(f"no measurement {check.measurement!r}", "measurement")
    if check.measurement == scale_reference:
        raise fields.error(
            f"{check.measurement!r} is the scale reference, which is never scored, so it "
            "cannot decide a check",
            "measurement",
        )
    if measurement.candidate != check.candidate:
        raise fields.error(
            f"the check is at candidate {check.candidate!r} but measurement "
            f"{check.measurement!r} belongs to {measurement.candidate!r}"
        )
    threshold = rules.thresholds.get(check.threshold)
    if threshold is None:
        raise fields.error(f"{check.threshold!r} is not in {rules.path}", "threshold")
    if measurement.status == "absent" and threshold.pass_when == "at_most":
        raise fields.error(
            f"measurement {check.measurement!r} is absent, but {check.threshold!r} passes "
            "at_most a distance; only a clearance (at_least) passes when the feature does not "
            "exist"
        )
    if check.review_threshold is not None:
        _validate_review_band(fields, check.review_threshold, threshold, rules)


def _validate_review_band(fields: Fields, review_name: str, fail: Threshold, rules: Rules) -> None:
    """The review line must sit on the passing side of the fail line, in the same direction."""
    review = rules.thresholds.get(review_name)
    if review is None:
        raise fields.error(f"{review_name!r} is not in {rules.path}", "review_threshold")
    if review.name == fail.name:
        raise fields.error("must differ from threshold", "review_threshold")
    if review.pass_when != fail.pass_when:
        raise fields.error(
            f"{review.name!r} passes {review.pass_when} but {fail.name!r} passes "
            f"{fail.pass_when}; a review band needs both in the same direction",
            "review_threshold",
        )
    # Strict: an equal value leaves no band, and values up to the fail line would pass unreviewed.
    inside = (
        review.value_ft < fail.value_ft
        if fail.pass_when == "at_most"
        else review.value_ft > fail.value_ft
    )
    if not inside:
        raise fields.error(
            f"{review.name!r} ({review.value_ft} ft) must be strictly on the passing side of "
            f"{fail.name!r} ({fail.value_ft} ft, {fail.pass_when}); equal values leave no "
            "band to review",
            "review_threshold",
        )


def _load_pipeline_measurement(fields: Fields, seen: Container[str]) -> PipelineMeasurement:
    measurement_id = _unique_id(fields, seen, "measurement")
    fields.path += f" ({measurement_id})"
    value = fields.optional_length("value_ft")
    if value is None:
        if fields.has("plus_minus_ft"):
            raise fields.error("a null value has no uncertainty; remove it", "plus_minus_ft")
        return PipelineMeasurement(
            measurement_id, None, None, fields.choice("missing", MISSING_REASONS)
        )
    if fields.has("missing"):
        raise fields.error("only a null value_ft gives a reason it is missing", "missing")
    return PipelineMeasurement(measurement_id, value, fields.optional_length("plus_minus_ft"), None)


def load_results(path: Path) -> Results:
    """Load and validate one pipeline run's results file.

    Every measurement either reports a value or reports null with the reason it is missing.
    `outcomes` may be null: a run that makes no pass/unsure/fail decisions records none. Matching
    the run to a survey happens in `load_study`.
    """
    data, _ = read_json(path)
    top = Fields(
        data,
        path,
        "",
        {
            "format",
            "unit",
            "pipeline",
            "capture",
            "rules_sha256",
            "scale_source",
            "measurements",
            "outcomes",
            "timing",
        },
    )
    top.header()
    rules_sha256 = top.text("rules_sha256")
    if not SHA256.fullmatch(rules_sha256):
        raise top.error(
            "expected the 64 lowercase hex digits of the rules file's sha256", "rules_sha256"
        )

    measurements: dict[str, PipelineMeasurement] = {}
    for index, entry in enumerate(top.items("measurements")):
        fields = top.child(
            entry, f"measurements[{index}]", {"id", "value_ft", "plus_minus_ft", "missing"}
        )
        measurement = _load_pipeline_measurement(fields, measurements)
        measurements[measurement.id] = measurement

    outcomes: dict[tuple[str, str], Outcome] | None = None
    if top.raw("outcomes") is not None:
        outcomes = {}
        for index, entry in enumerate(top.items("outcomes")):
            fields = top.child(entry, f"outcomes[{index}]", {"candidate", "check", "outcome"})
            key = (fields.text("candidate"), fields.text("check"))
            if key in outcomes:
                raise fields.error(f"check {key[1]!r} at {key[0]!r} is listed twice")
            outcomes[key] = fields.choice("outcome", OUTCOMES)

    timing = top.child(top.raw("timing"), "timing", {"capture_s", "processing_s"})
    return Results(
        path=path,
        pipeline=top.text("pipeline"),
        capture=top.text("capture"),
        rules_sha256=rules_sha256,
        scale_source=top.choice("scale_source", SCALE_SOURCES),
        measurements=measurements,
        outcomes=outcomes,
        capture_seconds=timing.optional_length("capture_s"),
        processing_seconds=timing.optional_length("processing_s"),
    )


def _checks_list(keys: Iterable[tuple[str, str]]) -> str:
    return ", ".join(f"{check} at {candidate}" for candidate, check in sorted(keys))


def _require_run_matches_survey(results: Results, truth: Truth, rules: Rules) -> None:
    """Reject a run that does not answer exactly the survey's questions under the same rules."""
    path = results.path
    if results.rules_sha256 != rules.sha256:
        raise InputError(
            f"{path}: rules_sha256: the run used rules hashing to {results.rules_sha256}, but "
            f"{rules.path} hashes to {rules.sha256}; every run must use the same frozen rules"
        )

    expected = set(truth.measurements)
    reported = set(results.measurements)
    unknown = sorted(reported - expected)
    if unknown:
        raise InputError(
            f"{path}: measurements {', '.join(map(repr, unknown))} are not in {truth.path}"
        )
    # The scale reference is optional: only runs that declare it as their scale source get it.
    missing = sorted(expected - reported - {truth.scale_reference})
    if missing:
        raise InputError(
            f"{path}: no entry for measurements {', '.join(map(repr, missing))}; report every "
            'survey measurement, with value_ft null and a "missing" reason when there is no value'
        )

    if results.outcomes is None:
        return
    expected_checks = {(check.candidate, check.check) for check in truth.checks}
    extra = set(results.outcomes) - expected_checks
    if extra:
        raise InputError(f"{path}: outcomes for checks not in the survey: {_checks_list(extra)}")
    absent = expected_checks - set(results.outcomes)
    if absent:
        raise InputError(
            f'{path}: no outcome for {_checks_list(absent)}; report "unsure" when the run '
            "cannot decide, or set outcomes to null for a run that makes no decisions"
        )


def _require_same_capture_time(results: Results, earlier: list[Results]) -> None:
    """Every row scored on one recording shares its walk time; none can claim a shorter one."""
    if results.capture_seconds is None:
        return
    for other in earlier:
        if other.capture == results.capture and other.capture_seconds not in (
            None,
            results.capture_seconds,
        ):
            raise InputError(
                f"{results.path}: timing.capture_s: {results.capture_seconds} s differs from "
                f"{other.capture_seconds} s in {other.path} for the same capture "
                f"{results.capture!r}; runs on one recording share its capture time"
            )


def load_study(rules_path: Path, truth_paths: list[Path], results_paths: list[Path]) -> Study:
    """Load every file and pair each run with the survey of the house it captured."""
    rules = load_rules(rules_path)
    truths: list[Truth] = []
    by_capture: dict[str, Truth] = {}
    for path in truth_paths:
        truth = load_truth(path, rules)
        for other in truths:
            if other.house == truth.house:
                raise InputError(
                    f"{path}: house {truth.house!r} is already surveyed in {other.path}"
                )
        for capture in truth.captures:
            if capture in by_capture:
                raise InputError(
                    f"{path}: capture {capture!r} already belongs to {by_capture[capture].path}"
                )
            by_capture[capture] = truth
        truths.append(truth)

    runs: dict[str, list[Results]] = {truth.house: [] for truth in truths}
    seen_runs: dict[tuple[str, str], Path] = {}
    for path in results_paths:
        results = load_results(path)
        truth = by_capture.get(results.capture)
        if truth is None:
            raise InputError(
                f"{path}: capture: no survey lists capture {results.capture!r}; add it to that "
                "house's captures"
            )
        key = (results.capture, results.pipeline)
        if key in seen_runs:
            raise InputError(
                f"{path}: pipeline {results.pipeline!r} on capture {results.capture!r} is "
                f"already scored from {seen_runs[key]}"
            )
        seen_runs[key] = path
        _require_run_matches_survey(results, truth, rules)
        _require_same_capture_time(results, runs[truth.house])
        runs[truth.house].append(results)

    return Study(
        rules=rules,
        houses=tuple(House(truth, tuple(runs[truth.house])) for truth in truths),
    )
