"""Turn a Measure Lab session zip into a results file the scorer reads.

The rig's measurements carry app-generated ids and meter values. A map file, written by hand after
the walk, says which session measurement and `values` key answers each survey measurement, and with
what uncertainty. The session format is Measure Lab's formatVersion 2, documented in
experiments/measure-lab/README.md ("Session format"). Only the fields used here are validated,
including each measurement's endpoints and the value keys they allow.
"""

import argparse
import hashlib
import json
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from typing import Any

from scoring.inputs import (
    FORMAT,
    UNIT,
    Fields,
    InputError,
    MissingReason,
    Outcome,
    PipelineMeasurement,
    Rules,
    Threshold,
    Truth,
    load_rules,
    load_study,
    load_truth,
    parse_json,
    read_json,
    refuse_output_over_inputs,
)
from scoring.metrics import decide

SESSION_FORMAT = "measure-lab-session"
SESSION_FORMAT_VERSION = 2
VALUE_KEYS = ("straight", "horizontal", "vertical", "alongWall", "gapToWall", "heightAboveGround")

# Exact by definition (international yard and pound agreement, 1959).
METERS_PER_FOOT = Decimal("0.3048")
# Division by 0.3048 rarely terminates (1 m is 3.28083989501312... ft), so feet are rounded to a
# millionth of a foot. ARKit is good to centimeters at best, so the rounding is invisible, and it
# keeps every value inside the scorer's 12-decimal-place limit.
FEET_PLACES = Decimal("0.000001")
# Capture time comes from the device-uptime clock, a Double of seconds; milliseconds are enough.
SECONDS_PLACES = Decimal("0.001")
# ARKit world tracking supplies metric scale; the rig uses no scale reference or depth model.
SCALE_SOURCE = "ar_poses"
# Marks a row whose outcomes the importer computed with the survey's rule.
RULE_SUFFIX = "+rule"

MAP_ENTRY_FIELDS = {"session_measurement", "key", "plus_minus_ft", "refusal"}

# The route check and its thresholds, named as in the scoring protocol. Measure Lab has no tool
# that records a routed cable path, so a route may only be mapped "unsupported".
ROUTE_CHECK = "route"
ROUTE_THRESHOLDS = frozenset({"max_route_ft", "review_route_ft"})

# A session zip holds every keyframe JPEG, and optional depth maps, so its size grows with the walk;
# hash it 1 MiB at a time instead of reading it whole.
HASH_CHUNK_BYTES = 1 << 20

# The most session.json may decompress to, so a crafted zip member cannot exhaust memory. A chosen
# safety bound, not calibrated against real captures: no field session has been measured yet.
MAX_SESSION_JSON_BYTES = 16 << 20

# The quantities Measure Lab's exporter (measuredValues in MeasureGeometry) writes for each kind of
# measurement. Point to point also gets alongWall exactly when it names a reference wall.
POINT_TO_POINT_KEYS = frozenset({"straight", "horizontal", "vertical"})
POINT_TO_WALL_KEYS = frozenset({"gapToWall", "heightAboveGround"})


def meters_to_feet(meters: Decimal) -> Decimal:
    """Exact division by 0.3048, then half-up rounding to FEET_PLACES."""
    with localcontext() as context:
        context.prec = 50
        return (meters / METERS_PER_FOOT).quantize(FEET_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class SessionMeasurement:
    """One measurement from a session.json measurements array.

    `id` is the app-generated id Measure Lab assigns. `time` is seconds on the device-uptime
    clock. `values` holds meters, and only the quantities the endpoints allow. `compared` names
    the quantity the rig validated, the one `accepted` vouches for. A measurement that is not
    accepted carries warnings. A map entry on it imports as missing failed, not as a value.
    """

    id: str
    time: Decimal
    values: dict[str, Decimal]
    compared: str
    accepted: bool


@dataclass(frozen=True)
class Session:
    """A session zip listed in the survey's captures, parsed.

    `measurements` is keyed by id. `zip_sha256` is the capture id the results file carries.
    `member` locates session.json in the zip, at its root or in its single session folder.
    `started_at_uptime` is when the walk began, on the device-uptime clock, and the run's
    capture_s runs from there to the last measurement. `refusals` holds the ids the operator
    refused to take, which a FromRefusal entry must name.
    """

    zip_path: Path
    zip_sha256: str
    member: str
    id: str
    started_at_uptime: Decimal
    measurements: dict[str, SessionMeasurement]
    refusals: set[str]


def _number(value: Any, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise InputError(f"{where}: expected a number, got {value!r}")
    return Decimal(value)


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise InputError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def _list(data: dict[str, Any], key: str, where: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise InputError(f"{where}: expected a list in {key!r}, got {value!r}")
    return value


def _session_member(archive: zipfile.ZipFile, path: Path) -> str:
    """The one session.json, at the zip's root or inside its single session folder."""
    found = [
        name
        for name in archive.namelist()
        if not name.startswith("__MACOSX/")
        and name.split("/")[-1] == "session.json"
        and name.count("/") <= 1
    ]
    if len(found) != 1:
        where = "none" if not found else ", ".join(found)
        raise InputError(
            f"{path}: expected exactly one session.json at the top of the zip or in its session "
            f"folder, found {where}; share the session from Measure Lab's Session sheet"
        )
    return found[0]


def sha256_file(path: Path) -> str:
    """The file's sha256, read in HASH_CHUNK_BYTES pieces so a session zip with many keyframes is
    never held in memory whole."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as error:
        raise InputError(f"{path}: cannot read ({error.strerror})") from None
    return digest.hexdigest()


def _ids(data: dict[str, Any], key: str, where: str) -> set[str]:
    ids: set[str] = set()
    for index, entry in enumerate(_list(data, key, where)):
        if not isinstance(entry, dict):
            raise InputError(f"{where}: {key}[{index}]: expected an object")
        entry_id = _text(entry.get("id"), f"{where}: {key}[{index}].id")
        if entry_id in ids:
            raise InputError(f"{where}: {key}[{index}].id: {entry_id!r} is listed twice")
        ids.add(entry_id)
    return ids


def _check_endpoints(
    entry: dict[str, Any],
    measurement: SessionMeasurement,
    points: set[str],
    walls: set[str],
    at: str,
) -> None:
    """The endpoints and value keys must be a pair Measure Lab's exporter can write."""
    source = _text(entry.get("from"), f"{at}.from")
    if source not in points:
        raise InputError(f"{at}.from: {source!r} is not a point id in points")
    target = _text(entry.get("to"), f"{at}.to")
    # The exporter omits a nil referenceWall, so a missing key and null mean the same.
    reference = entry.get("referenceWall")
    if reference is not None:
        reference = _text(reference, f"{at}.referenceWall")
    if target in walls:
        if reference is not None:
            raise InputError(
                f"{at}.referenceWall: a point-to-wall measurement has no reference wall, got "
                f"{reference!r}"
            )
        expected = POINT_TO_WALL_KEYS
        kind = "point to wall"
    elif target in points:
        if reference is None:
            expected = POINT_TO_POINT_KEYS
        elif reference in walls:
            expected = POINT_TO_POINT_KEYS | {"alongWall"}
        else:
            raise InputError(f"{at}.referenceWall: {reference!r} is not a wall id in walls")
        kind = "point to point" + (" with a reference wall" if reference is not None else "")
    else:
        raise InputError(f"{at}.to: {target!r} is not a point or wall id")
    keys = set(measurement.values)
    if keys != expected:
        raise InputError(
            f"{at}.values: a {kind} measurement has exactly {', '.join(sorted(expected))}, got "
            f"{', '.join(sorted(keys)) or 'none'}; Measure Lab does not write this combination"
        )
    if measurement.compared not in keys:
        raise InputError(
            f"{at}.compared: {measurement.compared!r} is not one of its values "
            f"({', '.join(sorted(keys))})"
        )


def load_session(path: Path, truth: Truth) -> Session:
    """Read a session zip listed in the survey's captures. The listing is checked from the zip's
    hash before anything is decompressed, so an unvetted zip is never unpacked."""
    zip_sha256 = sha256_file(path)
    if zip_sha256 not in truth.captures:
        raise InputError(
            f"{truth.path}: captures does not list {zip_sha256}, the sha256 of {path}; add it so "
            "this run is scored against this survey"
        )
    try:
        with zipfile.ZipFile(path) as archive:
            member = _session_member(archive, path)
            with archive.open(member) as handle:
                raw = handle.read(MAX_SESSION_JSON_BYTES + 1)
    except zipfile.BadZipFile:
        raise InputError(f"{path}: not a zip file") from None
    where = f"{path}!{member}"
    if len(raw) > MAX_SESSION_JSON_BYTES:
        raise InputError(
            f"{where}: session.json is larger than {MAX_SESSION_JSON_BYTES} bytes, the importer's "
            "limit; split the capture into shorter sessions"
        )
    data = parse_json(raw, where)
    if not isinstance(data, dict):
        raise InputError(f"{where}: expected a JSON object")

    if data.get("format") != SESSION_FORMAT:
        raise InputError(f"{where}: format is {data.get('format')!r}, not {SESSION_FORMAT!r}")
    version = data.get("formatVersion")
    if type(version) is not int or version != SESSION_FORMAT_VERSION:
        raise InputError(
            f"{where}: formatVersion is {version!r}; this importer reads Measure Lab session "
            f"format {SESSION_FORMAT_VERSION} only, whose value fields may differ in other versions"
        )
    units = data.get("units")
    if not isinstance(units, dict) or units.get("length") != "meters":
        raise InputError(f"{where}: units.length must be 'meters'")

    info = data.get("session")
    if not isinstance(info, dict):
        raise InputError(f"{where}: session: expected an object")
    session_id = _text(info.get("id"), f"{where}: session.id")
    started = _number(info.get("startedAtUptime"), f"{where}: session.startedAtUptime")

    points = _ids(data, "points", where)
    walls = _ids(data, "walls", where)
    shared = sorted(points & walls)
    if shared:
        raise InputError(f"{where}: {', '.join(map(repr, shared))} is both a point and a wall id")

    measurements: dict[str, SessionMeasurement] = {}
    for index, entry in enumerate(_list(data, "measurements", where)):
        at = f"{where}: measurements[{index}]"
        if not isinstance(entry, dict):
            raise InputError(f"{at}: expected an object")
        measurement_id = _text(entry.get("id"), f"{at}.id")
        if measurement_id in measurements:
            raise InputError(f"{at}.id: measurement {measurement_id!r} is listed twice")
        values = entry.get("values")
        if not isinstance(values, dict):
            raise InputError(f"{at} ({measurement_id}).values: expected an object")
        accepted = entry.get("accepted")
        if not isinstance(accepted, bool):
            raise InputError(f"{at} ({measurement_id}).accepted: expected true or false")
        measurement = SessionMeasurement(
            id=measurement_id,
            time=_number(entry.get("time"), f"{at} ({measurement_id}).time"),
            values={
                key: _number(value, f"{at} ({measurement_id}).values.{key}")
                for key, value in values.items()
            },
            compared=_text(entry.get("compared"), f"{at} ({measurement_id}).compared"),
            accepted=accepted,
        )
        _check_endpoints(entry, measurement, points, walls, f"{at} ({measurement_id})")
        measurements[measurement_id] = measurement

    refusals: set[str] = set()
    for index, entry in enumerate(_list(data, "refusals", where)):
        if not isinstance(entry, dict):
            raise InputError(f"{where}: refusals[{index}]: expected an object")
        refusals.add(_text(entry.get("id"), f"{where}: refusals[{index}].id"))

    return Session(
        zip_path=path,
        zip_sha256=zip_sha256,
        member=member,
        id=session_id,
        started_at_uptime=started,
        measurements=measurements,
        refusals=refusals,
    )


@dataclass(frozen=True)
class FromSession:
    """A map entry that answers a survey measurement with a session measurement's `values` key.

    `measurement` is the session measurement id and `key` the quantity to import, which must be
    that measurement's `compared` quantity. `plus_minus_ft` states the uncertainty in feet, from
    the entry or the map's `plus_minus_ft_by_key`.
    """

    measurement: str
    key: str
    plus_minus_ft: Decimal


@dataclass(frozen=True)
class FromRefusal:
    """A map entry for a survey measurement the operator refused to take.

    It imports as missing failed. `refusal` must be an id in the session's refusals.
    """

    refusal: str


@dataclass(frozen=True)
class Map:
    """The map file, written by hand after the walk, that ties session measurements to survey ones.

    `entries` maps a survey measurement id to a FromSession, a FromRefusal, or one of the bare
    strings "absent" or "unsupported". `pipeline` names the results row. `session` is the session
    id the map was written for, and must match the zip's. `path` names the file in error messages.
    """

    path: Path
    pipeline: str
    session: str
    entries: dict[str, FromSession | FromRefusal | MissingReason]


def load_map(path: Path) -> Map:
    """Read a map file into a Map.

    A FromSession entry needs a stated uncertainty, in its own plus_minus_ft or under its key in
    plus_minus_ft_by_key. There is no default, so an entry with neither is an error.
    """
    data, _ = read_json(path)
    top = Fields(
        data,
        path,
        "",
        {"format", "unit", "pipeline", "session", "notes", "plus_minus_ft_by_key", "measurements"},
    )
    top.header()
    if top.has("notes"):
        top.text("notes")
    by_key: dict[str, Decimal] = {}
    if top.has("plus_minus_ft_by_key"):
        table = top.raw("plus_minus_ft_by_key")
        keys = top.child(table, "plus_minus_ft_by_key", VALUE_KEYS)
        by_key = {key: keys.length(key) for key in keys.data}

    table = top.raw("measurements")
    if not isinstance(table, dict) or not table:
        raise top.error("expected an object keyed by survey measurement id", "measurements")
    entries: dict[str, FromSession | FromRefusal | MissingReason] = {}
    for survey_id, entry in table.items():
        where = f"measurements.{survey_id}"
        if entry in ("absent", "unsupported"):
            entries[survey_id] = entry
            continue
        if isinstance(entry, str):
            raise top.error(f'expected "absent", "unsupported" or an object, got {entry!r}', where)
        fields = top.child(entry, where, MAP_ENTRY_FIELDS)
        if fields.has("refusal"):
            extra = sorted(set(fields.data) - {"refusal"})
            if extra:
                raise fields.error(f"a refusal entry takes no {', '.join(extra)}")
            entries[survey_id] = FromRefusal(fields.text("refusal"))
            continue
        key = fields.choice("key", VALUE_KEYS)
        if fields.has("plus_minus_ft"):
            plus_minus = fields.length("plus_minus_ft")
        elif key in by_key:
            plus_minus = by_key[key]
        else:
            raise fields.error(
                f"no uncertainty for values key {key!r}; state plus_minus_ft here or in "
                "plus_minus_ft_by_key"
            )
        entries[survey_id] = FromSession(fields.text("session_measurement"), key, plus_minus)

    return Map(path, top.text("pipeline"), top.text("session"), entries)


def _route_measurements(truth: Truth) -> set[str]:
    """Survey measurements that decide a route check, by check name or by a route threshold."""
    return {
        check.measurement
        for check in truth.checks
        if check.check == ROUTE_CHECK
        or check.threshold in ROUTE_THRESHOLDS
        or check.review_threshold in ROUTE_THRESHOLDS
    }


def _check_map(mapping: Map, session: Session, truth: Truth) -> None:
    where = str(mapping.path)
    if mapping.session != session.id:
        raise InputError(
            f"{where}: session is {mapping.session!r}, but {session.zip_path} holds session "
            f"{session.id!r}; this map was written for another walk"
        )
    unknown = sorted(set(mapping.entries) - set(truth.measurements))
    if unknown:
        raise InputError(
            f"{where}: measurements {', '.join(map(repr, unknown))} are not survey measurement "
            f"ids in {truth.path}"
        )
    # The scale reference may be left out, as in a results file: the rig does not use it.
    left_out = sorted(set(truth.measurements) - set(mapping.entries) - {truth.scale_reference})
    if left_out:
        raise InputError(
            f"{where}: no entry for survey measurements {', '.join(map(repr, left_out))}; map "
            'each to a session measurement, a refusal, "absent" or "unsupported"'
        )
    routes = _route_measurements(truth)
    for survey_id, entry in mapping.entries.items():
        at = f"{where}: measurements.{survey_id}"
        if survey_id in routes and entry != "unsupported":
            raise InputError(
                f'{at}: {survey_id!r} decides a route check, so map it as "unsupported". Measure '
                "Lab records no routed cable path, and a point or wall distance leaves out the "
                "route's vertical legs and detours"
            )
        if isinstance(entry, FromRefusal) and entry.refusal not in session.refusals:
            raise InputError(f"{at}.refusal: {session.member} has no refusal {entry.refusal!r}")
        if not isinstance(entry, FromSession):
            continue
        measurement = session.measurements.get(entry.measurement)
        if measurement is None:
            raise InputError(
                f"{at}.session_measurement: {session.member} has no measurement "
                f"{entry.measurement!r}"
            )
        if entry.key not in measurement.values:
            raise InputError(
                f"{at}.key: measurement {entry.measurement!r} has no {entry.key!r} value; it has "
                f"{', '.join(measurement.values) or 'none'}"
            )
        if entry.key != measurement.compared:
            raise InputError(
                f"{at}.key: {entry.key!r} was not the validated quantity for measurement "
                f"{entry.measurement!r}; its compared field is {measurement.compared!r}"
            )
        if measurement.accepted and measurement.values[entry.key] < 0:
            raise InputError(
                f"{at}: measurement {entry.measurement!r} is accepted but its {entry.key} is "
                f"{measurement.values[entry.key]} m; format 2 only allows a negative "
                "heightAboveGround, and marks it belowGround and not accepted"
            )


def _imported_measurement(
    survey_id: str, entry: FromSession | FromRefusal | MissingReason, session: Session
) -> PipelineMeasurement:
    if isinstance(entry, str):
        return PipelineMeasurement(survey_id, None, None, entry)
    if isinstance(entry, FromRefusal):
        return PipelineMeasurement(survey_id, None, None, "failed")
    measurement = session.measurements[entry.measurement]
    if not measurement.accepted:
        # The rig's own abstention: a value with warnings that its scoring counts as no answer.
        return PipelineMeasurement(survey_id, None, None, "failed")
    value_ft = meters_to_feet(measurement.values[entry.key])
    return PipelineMeasurement(survey_id, value_ft, entry.plus_minus_ft, None)


def _measurement_json(measurement: PipelineMeasurement) -> dict[str, Any]:
    if measurement.value_ft is None:
        return {"id": measurement.id, "value_ft": None, "missing": measurement.missing}
    return {
        "id": measurement.id,
        "value_ft": measurement.value_ft,
        "plus_minus_ft": measurement.plus_minus_ft,
    }


def _rule_outcome(
    measurement: PipelineMeasurement, threshold: Threshold, review: Threshold | None
) -> Outcome:
    """The survey's strict rule applied to the run's own value. Without a value it never decides,
    except that a feature the operator marked absent clears an at_least check, as in the survey."""
    if measurement.value_ft is None:
        absent_clearance = measurement.missing == "absent" and threshold.pass_when == "at_least"
        return "pass" if absent_clearance else "unsure"
    assert measurement.plus_minus_ft is not None  # the map always states an uncertainty
    result = decide(measurement.value_ft, measurement.plus_minus_ft, threshold, review)
    return "unsure" if result == "borderline" or result == "review" else result


def _outcomes(
    truth: Truth, rules: Rules, measurements: dict[str, PipelineMeasurement]
) -> list[dict[str, str]]:
    outcomes = []
    for check in truth.checks:
        threshold = rules.thresholds[check.threshold]
        review = (
            None if check.review_threshold is None else rules.thresholds[check.review_threshold]
        )
        outcome = _rule_outcome(measurements[check.measurement], threshold, review)
        outcomes.append({"candidate": check.candidate, "check": check.check, "outcome": outcome})
    return outcomes


def build_results(
    session: Session, mapping: Map, rules: Rules, truth: Truth, *, decide_outcomes: bool
) -> dict[str, Any]:
    """Build the results dict the scorer reads, from a session, a map, the rules and the survey.

    The map must be written for this session's id, leave out no survey measurement except the
    scale reference, and map each measurement that decides a route check "unsupported", because
    Measure Lab records no routed cable path. The zip's sha256 must already be in the survey's
    captures. With decide_outcomes, the pipeline id gains the RULE_SUFFIX and outcomes follow the
    survey's strict rule on the run's own values. Without decide_outcomes, the row makes no
    decisions.
    """
    _check_map(mapping, session, truth)
    if session.zip_sha256 not in truth.captures:
        raise InputError(
            f"{truth.path}: captures does not list {session.zip_sha256}, the sha256 of "
            f"{session.zip_path}; add it so this run is scored against this survey"
        )
    measurements = {
        survey_id: _imported_measurement(survey_id, mapping.entries[survey_id], session)
        for survey_id in truth.measurements
        if survey_id in mapping.entries
    }
    times = [measurement.time for measurement in session.measurements.values()]
    capture_s = None
    if times:
        capture_s = (max(times) - session.started_at_uptime).quantize(
            SECONDS_PLACES, rounding=ROUND_HALF_UP
        )
        if capture_s < 0:
            raise InputError(f"{session.zip_path}: a measurement predates startedAtUptime")
    return {
        "format": FORMAT,
        "unit": UNIT,
        "pipeline": mapping.pipeline + (RULE_SUFFIX if decide_outcomes else ""),
        "capture": session.zip_sha256,
        "rules_sha256": rules.sha256,
        "scale_source": SCALE_SOURCE,
        "measurements": [_measurement_json(m) for m in measurements.values()],
        "outcomes": _outcomes(truth, rules, measurements) if decide_outcomes else None,
        # The rig shows each value as it is tapped and records no processing time, so
        # processing_s is null; 0 would claim a measurement nobody took.
        "timing": {"capture_s": capture_s, "processing_s": None},
    }


def dumps(value: Any, indent: str = "") -> str:
    """JSON text with every Decimal written exactly, never through a float."""
    inner = indent + "  "
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{inner}{_string(key)}: {dumps(item, inner)}" for key, item in value.items()]
        return "{\n" + ",\n".join(items) + f"\n{indent}}}"
    if isinstance(value, list):
        if not value:
            return "[]"
        return "[\n" + ",\n".join(inner + dumps(item, inner) for item in value) + f"\n{indent}]"
    if isinstance(value, Decimal):
        return format(value, "f")
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _string(value)
    raise TypeError(f"cannot write {type(value).__name__} as JSON")


def _string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def import_session(
    session_path: Path,
    map_path: Path,
    rules_path: Path,
    truth_path: Path,
    out_path: Path,
    *,
    decide_outcomes: bool,
) -> dict[str, Any]:
    """Import a session zip, map, rules file and survey into a results file, and return the results.

    An output path that collides with any input, by the same path, another spelling, a symlink or
    a hard link, is refused before anything is read. The results text is then loaded through the
    full scorer in a scratch directory, so a file that would not score is never written.
    """
    refuse_output_over_inputs(
        [out_path],
        [
            ("session zip", session_path),
            ("map", map_path),
            ("rules", rules_path),
            ("truth", truth_path),
        ],
    )
    rules = load_rules(rules_path)
    truth = load_truth(truth_path, rules)
    session = load_session(session_path, truth)
    results = build_results(
        session, load_map(map_path), rules, truth, decide_outcomes=decide_outcomes
    )
    text = dumps(results) + "\n"
    # Load the output through the scorer first, so a file that would not score is never written.
    with tempfile.TemporaryDirectory() as scratch:
        candidate = Path(scratch) / "results.json"
        candidate.write_text(text, encoding="utf-8")
        load_study(rules_path, [truth_path], [candidate])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return results


def main(argv: list[str]) -> int:
    """Run `score import-measure-lab` with the given argv.

    An InputError prints a `score import-measure-lab:` line on stderr and returns 2, with no
    output file written. Success writes the results file and returns 0.
    """
    parser = argparse.ArgumentParser(
        prog="score import-measure-lab",
        description="Convert a Measure Lab session zip (format 2) into a results file, using a "
        "map file that ties session measurements to survey measurement ids.",
    )
    parser.add_argument("session", type=Path, help="session zip shared from Measure Lab")
    parser.add_argument("--map", type=Path, required=True, help="map file (JSON)")
    parser.add_argument("--rules", type=Path, required=True, help="rules file (JSON)")
    parser.add_argument("--truth", type=Path, required=True, help="survey file for this house")
    parser.add_argument("--out", type=Path, required=True, help="results file to write")
    parser.add_argument(
        "--decide",
        action="store_true",
        help=f"compute pass/unsure/fail with the survey's strict rule and add {RULE_SUFFIX} "
        "to the pipeline id; without it the row makes no decisions",
    )
    args = parser.parse_args(argv)
    try:
        results = import_session(
            args.session,
            args.map,
            args.rules,
            args.truth,
            args.out,
            decide_outcomes=args.decide,
        )
    except InputError as error:
        print(f"score import-measure-lab: {error}", file=sys.stderr)
        return 2
    print(
        f"score import-measure-lab: wrote {args.out} (pipeline {results['pipeline']}, "
        f"capture {results['capture']})",
        file=sys.stderr,
    )
    return 0
