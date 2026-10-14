"""The Measure Lab importer on a synthetic format-2 session, with hand-computed feet.

The committed session (fixtures/measure-lab/synthetic-session-01) holds, in meters:
m1 scale 1.524 (5 ft) point to point; m2 wall 9.87552 (32.4 ft) along the wall; m3 c1 gas, straight
from the footprint edge p10 to the regulator p5, 1.4 (4.593175853... ft, written 4.593176); m4 c1
facing, a gapToWall of 1.49352 (4.9 ft); m5 and m7, along-wall distances the map leaves unused; m6
c2 gas, straight from p11 to p8, 0.95 but not accepted because p8 is on ARKit's estimated plane;
and refusal r1 for the c2 facing gap. The
map marks both routes unsupported, because Measure Lab records no routed cable path. The session
starts at uptime 1000.25 and the last measurement is at 1412.75.
"""

import copy
import csv
import dataclasses
import hashlib
import json
import math
import os
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from helpers import FIXTURES, MEASURE_LAB, SESSION_ZIP, committed_session, session_zip

from scoring import measure_lab
from scoring.cli import main
from scoring.inputs import (
    InputError,
    PipelineMeasurement,
    Threshold,
    load_rules,
    load_study,
    load_truth,
)
from scoring.measure_lab import (
    HASH_CHUNK_BYTES,
    _rule_outcome,
    import_session,
    load_session,
    meters_to_feet,
    sha256_file,
)

D = Decimal
RULES = FIXTURES / "rules.json"
TRUTH = FIXTURES / "truth" / "synthetic-01.json"
MAP = MEASURE_LAB / "map.json"


def committed_map() -> dict[str, Any]:
    return json.loads(MAP.read_text())


class Setup:
    """Editable copies of the committed session, map and survey, written to a temp dir."""

    def __init__(self, root: Path):
        self.root = root
        self.session = committed_session()
        self.map = committed_map()
        self.truth = json.loads(TRUTH.read_text())

    def run(self, *, decide: bool = False) -> dict[str, Any]:
        zip_path = session_zip(self.session, self.root / "session.zip")
        capture = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        if capture not in self.truth["captures"]:
            self.truth["captures"].append(capture)
        map_path = self.root / "map.json"
        map_path.write_text(json.dumps(self.map))
        truth_path = self.root / "truth.json"
        truth_path.write_text(json.dumps(self.truth))
        return import_session(
            zip_path, map_path, RULES, truth_path, self.root / "out.json", decide_outcomes=decide
        )

    def error(self, **kwargs: Any) -> str:
        with pytest.raises(InputError) as caught:
            self.run(**kwargs)
        assert not (self.root / "out.json").exists()
        return str(caught.value)

    def measurement(self, measurement_id: str) -> dict[str, Any]:
        return next(m for m in self.session["measurements"] if m["id"] == measurement_id)


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


def by_id(results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in results["measurements"]}


class TestMetersToFeet:
    @pytest.mark.parametrize(
        ("meters", "feet"),
        [
            ("0.3048", "1.000000"),
            ("9.87552", "32.400000"),
            ("1.4", "4.593176"),  # 4.59317585301837...
            ("1", "3.280840"),  # 3.28083989501312...
            ("0.0000001524", "0.000001"),  # exactly 0.0000005 ft: half rounds up
            ("0.0000001523", "0.000000"),  # 0.00000049967... ft
            ("0", "0.000000"),
        ],
    )
    def test_exact_conversion_then_six_places(self, meters, feet):
        result = meters_to_feet(D(meters))
        assert str(result) == feet
        assert result.as_tuple().exponent == -6


def test_committed_zip_is_the_committed_session(tmp_path: Path):
    rebuilt = session_zip(
        (MEASURE_LAB / "synthetic-session-01" / "session.json").read_bytes(),
        tmp_path / "rebuilt.zip",
    )
    assert rebuilt.read_bytes() == SESSION_ZIP.read_bytes(), (
        "fixtures/measure-lab/synthetic-session-01.zip is stale; run "
        "`uv run python tests/helpers.py rebuild-session-zip` and put the printed sha256 in "
        "fixtures/truth/synthetic-01.json captures"
    )
    capture = hashlib.sha256(SESSION_ZIP.read_bytes()).hexdigest()
    assert capture in json.loads(TRUTH.read_text())["captures"]


class TestImport:
    def test_values_in_feet_with_the_mapped_uncertainty(self, setup: Setup):
        results = setup.run()
        entries = by_id(results)
        assert [entry["id"] for entry in results["measurements"]] == [
            "scale",
            "wall-length",
            "c1-gas",
            "c1-facing",
            "c1-route",
            "c1-pool",
            "c2-gas",
            "c2-facing",
            "c2-route",
            "c2-pool",
        ]
        assert entries["scale"]["value_ft"] == D("5.000000")
        assert entries["wall-length"]["value_ft"] == D("32.400000")
        assert entries["c1-gas"]["value_ft"] == D("4.593176")
        assert entries["c1-facing"]["value_ft"] == D("4.900000")
        assert {entry.get("plus_minus_ft") for entry in entries.values() if entry["value_ft"]} == {
            D("0.3")
        }

    def test_missing_entries(self, setup: Setup):
        entries = by_id(setup.run())
        assert entries["c1-pool"] == {"id": "c1-pool", "value_ft": None, "missing": "absent"}
        assert entries["c2-facing"] == {"id": "c2-facing", "value_ft": None, "missing": "failed"}
        assert entries["c2-pool"] == {"id": "c2-pool", "value_ft": None, "missing": "unsupported"}
        for route in ("c1-route", "c2-route"):
            assert entries[route] == {"id": route, "value_ft": None, "missing": "unsupported"}

    def test_not_accepted_becomes_failed(self, setup: Setup):
        # m6 starts at a ground point on ARKit's estimated plane: warning estimatedPlane.
        assert by_id(setup.run())["c2-gas"] == {
            "id": "c2-gas",
            "value_ft": None,
            "missing": "failed",
        }

    def test_not_accepted_with_a_positive_value_also_becomes_failed(self, setup: Setup):
        m3 = setup.measurement("m3")
        m3.update(accepted=False, warnings=["estimatedPlane"])
        assert by_id(setup.run())["c1-gas"]["missing"] == "failed"

    def test_accepted_straight_does_not_validate_unchecked_along_wall(self, setup: Setup):
        m2 = setup.measurement("m2")
        m2.update(compared="straight", accepted=True, warnings=[])
        setup.session["walls"][0].update(validations=[], warnings=["wallNotValidated"])
        error = setup.error()
        assert "map.json: measurements.wall-length.key" in error
        assert "'alongWall'" in error and "'straight'" in error

    def test_missing_compared_quantity_is_rejected(self, setup: Setup):
        del setup.measurement("m5")["compared"]
        assert "measurements[4] (m5).compared: expected a non-empty string" in setup.error()

    def test_header_timing_and_ids(self, setup: Setup):
        results = setup.run()
        zip_bytes = (setup.root / "session.zip").read_bytes()
        assert results["capture"] == hashlib.sha256(zip_bytes).hexdigest()
        assert results["rules_sha256"] == hashlib.sha256(RULES.read_bytes()).hexdigest()
        assert results["pipeline"] == "measure-lab"
        assert results["scale_source"] == "ar_poses"
        assert results["outcomes"] is None
        assert results["timing"] == {"capture_s": D("412.500"), "processing_s": None}

    def test_per_entry_uncertainty_overrides_the_key_default(self, setup: Setup):
        setup.map["measurements"]["c1-gas"]["plus_minus_ft"] = 0.25
        assert by_id(setup.run())["c1-gas"]["plus_minus_ft"] == D("0.25")

    def test_scale_reference_may_be_left_out(self, setup: Setup):
        del setup.map["measurements"]["scale"]
        assert "scale" not in by_id(setup.run())

    def test_written_file_loads_with_exact_numbers(self, setup: Setup):
        setup.run()
        text = (setup.root / "out.json").read_text()
        assert '"value_ft": 4.593176' in text
        assert '"capture_s": 412.500' in text


class TestDecide:
    def test_outcomes_follow_the_strict_rule(self, setup: Setup):
        results = setup.run(decide=True)
        assert results["pipeline"] == "measure-lab+rule"
        outcomes = {(o["candidate"], o["check"]): o["outcome"] for o in results["outcomes"]}
        assert outcomes == {
            ("c1", "gas"): "pass",  # 4.593176 - 3 = 1.593176 > 0.3
            ("c1", "facing_gap"): "pass",  # 4.9 - 3 = 1.9 > 0.3
            ("c1", "route"): "unsure",  # unsupported: no routed path
            ("c1", "pool"): "pass",  # the operator asserted no pool: an absent feature clears it
            ("c2", "gas"): "unsure",  # not accepted: no value
            ("c2", "facing_gap"): "unsure",  # refused: no value
            ("c2", "route"): "unsure",  # unsupported: no routed path
            ("c2", "pool"): "unsure",  # unsupported
        }

    @pytest.mark.parametrize(
        ("meters", "outcome"),
        [
            ("1.00584", "unsure"),  # 3.3 ft: margin 0.3 equals the uncertainty
            ("1.00587", "pass"),  # 3.300098 ft
            ("0.82296", "unsure"),  # 2.7 ft: miss equals the uncertainty
            ("0.82293", "fail"),  # 2.699902 ft
        ],
    )
    def test_equality_is_unsure(self, setup: Setup, meters, outcome):
        setup.measurement("m3")["values"]["straight"] = float(meters)
        outcomes = {
            (o["candidate"], o["check"]): o["outcome"] for o in setup.run(decide=True)["outcomes"]
        }
        assert outcomes[("c1", "gas")] == outcome

    def test_absent_cannot_pass_an_at_most_rule(self):
        limit = Threshold("max_route_ft", D(20), "at_most", "synthetic")
        absent = PipelineMeasurement("c1-route", None, None, "absent")
        assert _rule_outcome(absent, limit, None) == "unsure"


def edit_session(key: str, value: Any):
    return lambda setup: setup.session.update({key: value})


ERRORS: list[tuple[str, Any, str]] = [
    (
        "unknown survey id",
        lambda s: s.map["measurements"].update({"c1-door": "absent"}),
        "measurements 'c1-door' are not survey measurement ids",
    ),
    (
        "missing session measurement",
        lambda s: s.map["measurements"]["c1-gas"].update(session_measurement="m99"),
        "measurements.c1-gas.session_measurement: synthetic-session-01/session.json has no "
        "measurement 'm99'",
    ),
    (
        "missing refusal",
        lambda s: s.map["measurements"].update({"c2-facing": {"refusal": "r9"}}),
        "measurements.c2-facing.refusal: synthetic-session-01/session.json has no refusal 'r9'",
    ),
    (
        "values key absent from the measurement",
        lambda s: s.map["measurements"]["c1-gas"].update(key="alongWall"),
        "measurements.c1-gas.key: measurement 'm3' has no 'alongWall' value; it has straight, "
        "horizontal, vertical",
    ),
    ("format version 1", edit_session("formatVersion", 1), "formatVersion is 1; this importer"),
    ("format version 3", edit_session("formatVersion", 3), "formatVersion is 3; this importer"),
    (
        "format name",
        edit_session("format", "something-else"),
        "format is 'something-else', not 'measure-lab-session'",
    ),
    (
        "survey measurement left out",
        lambda s: s.map["measurements"].pop("c2-pool"),
        "no entry for survey measurements 'c2-pool'",
    ),
    (
        "map for another session",
        lambda s: s.map.update(session="other-session"),
        "session is 'other-session', but",
    ),
    (
        "no uncertainty",
        lambda s: s.map.pop("plus_minus_ft_by_key"),
        "measurements.scale: no uncertainty for values key 'straight'",
    ),
    (
        "unknown values key",
        lambda s: s.map["measurements"]["c1-gas"].update(key="diagonal"),
        "measurements.c1-gas.key: expected one of straight, horizontal",
    ),
    (
        "unknown map string",
        lambda s: s.map["measurements"].update({"c2-pool": "failed"}),
        'measurements.c2-pool: expected "absent", "unsupported" or an object',
    ),
    (
        "refusal with extra fields",
        lambda s: s.map["measurements"].update({"c2-facing": {"refusal": "r1", "key": "straight"}}),
        "a refusal entry takes no key",
    ),
    (
        "accepted negative value",
        lambda s: s.measurement("m3")["values"].update(straight=-1.4),
        "measurement 'm3' is accepted but its straight is -1.4 m",
    ),
    (
        "point to wall carrying straight",
        lambda s: [
            s.measurement("m4")["values"].update(straight=3.0),
            s.measurement("m4").update(compared="straight"),
        ],
        "measurements[3] (m4).values: a point to wall measurement has exactly gapToWall, "
        "heightAboveGround, got gapToWall, heightAboveGround, straight",
    ),
    (
        "compared not among the values",
        lambda s: s.measurement("m4").update(compared="straight"),
        "measurements[3] (m4).compared: 'straight' is not one of its values",
    ),
    (
        "unknown target",
        lambda s: s.measurement("m4").update(to="no-such-wall"),
        "measurements[3] (m4).to: 'no-such-wall' is not a point or wall id",
    ),
    (
        "unknown source",
        lambda s: s.measurement("m3").update({"from": "p99"}),
        "measurements[2] (m3).from: 'p99' is not a point id in points",
    ),
    (
        "along-wall without a reference wall",
        lambda s: s.measurement("m5").update(referenceWall=None),
        "measurements[4] (m5).values: a point to point measurement has exactly horizontal, "
        "straight, vertical, got alongWall, horizontal, straight, vertical",
    ),
    (
        "reference wall missing from walls",
        lambda s: s.measurement("m5").update(referenceWall="w9"),
        "measurements[4] (m5).referenceWall: 'w9' is not a wall id in walls",
    ),
    (
        "reference wall on a point-to-wall measurement",
        lambda s: s.measurement("m4").update(referenceWall="w1"),
        "measurements[3] (m4).referenceWall: a point-to-wall measurement has no reference wall",
    ),
    (
        "from as a list",
        lambda s: s.measurement("m3").update({"from": ["w1"]}),
        "measurements[2] (m3).from: expected a non-empty string, got ['w1']",
    ),
    (
        "from as a object",
        lambda s: s.measurement("m3").update({"from": {"id": "w1"}}),
        "measurements[2] (m3).from: expected a non-empty string, got {'id': 'w1'}",
    ),
    (
        "to as a list",
        lambda s: s.measurement("m4").update({"to": ["w1"]}),
        "measurements[3] (m4).to: expected a non-empty string, got ['w1']",
    ),
    (
        "to as a object",
        lambda s: s.measurement("m4").update({"to": {"id": "w1"}}),
        "measurements[3] (m4).to: expected a non-empty string, got {'id': 'w1'}",
    ),
    (
        "referenceWall as a list",
        lambda s: s.measurement("m5").update({"referenceWall": ["w1"]}),
        "measurements[4] (m5).referenceWall: expected a non-empty string, got ['w1']",
    ),
    (
        "referenceWall as a object",
        lambda s: s.measurement("m5").update({"referenceWall": {"id": "w1"}}),
        "measurements[4] (m5).referenceWall: expected a non-empty string, got {'id': 'w1'}",
    ),
    (
        "id shared by a point and a wall",
        lambda s: s.session["points"].append({**s.session["points"][0], "id": "w1"}),
        "'w1' is both a point and a wall id",
    ),
    (
        "duplicate point id",
        lambda s: s.session["points"].append(copy.deepcopy(s.session["points"][0])),
        "points[11].id: 'p1' is listed twice",
    ),
    (
        "length units",
        lambda s: s.session["units"].update(length="feet"),
        "units.length must be 'meters'",
    ),
]


@pytest.mark.parametrize(("edit", "expected"), [e[1:] for e in ERRORS], ids=[e[0] for e in ERRORS])
def test_errors(setup: Setup, edit, expected):
    edit(setup)
    assert expected in setup.error()


@pytest.mark.parametrize(
    "entry",
    [
        {"session_measurement": "m5", "key": "alongWall"},  # accepted, compared alongWall
        {"session_measurement": "m2", "key": "alongWall"},
        {"refusal": "r1"},
        "absent",
    ],
    ids=["along-wall m5", "along-wall m2", "refusal", "absent"],
)
@pytest.mark.parametrize("route", ["c1-route", "c2-route"])
def test_route_must_be_unsupported(setup: Setup, route: str, entry: Any):
    setup.map["measurements"][route] = entry
    error = setup.error(decide=True)
    assert f'measurements.{route}: {route!r} decides a route check, so map it as "unsupported"' in (
        error
    )


def test_route_is_recognised_by_its_threshold_under_another_check_name(setup: Setup):
    for check in setup.truth["checks"]:
        if check["check"] == "route":
            check["check"] = "cable"
    setup.map["measurements"]["c1-route"] = {"session_measurement": "m5", "key": "alongWall"}
    assert "'c1-route' decides a route check" in setup.error()


def test_omitted_reference_wall_is_the_exporter_shape(setup: Setup):
    # Swift's synthesized Codable leaves a nil referenceWall out instead of writing null.
    for measurement in setup.session["measurements"]:
        if measurement["referenceWall"] is None:
            del measurement["referenceWall"]
    assert by_id(setup.run())["c1-gas"]["value_ft"] == D("4.593176")


def test_oversized_session_json_is_refused(setup: Setup, monkeypatch):
    monkeypatch.setattr(measure_lab, "MAX_SESSION_JSON_BYTES", 100)
    assert "session.json is larger than 100 bytes, the importer's limit" in setup.error()


def test_session_json_at_the_limit_is_read(setup: Setup, monkeypatch):
    size = len((json.dumps(setup.session, indent=2) + "\n").encode())
    monkeypatch.setattr(measure_lab, "MAX_SESSION_JSON_BYTES", size)
    assert setup.run()["pipeline"] == "measure-lab"


def listed_truth(tmp_path: Path, zip_path: Path) -> Path:
    """A copy of the survey whose captures list this zip, so the import gets past the listing."""
    truth = json.loads(TRUTH.read_text())
    truth["captures"].append(hashlib.sha256(zip_path.read_bytes()).hexdigest())
    path = tmp_path / "truth.json"
    path.write_text(json.dumps(truth))
    return path


def test_zip_without_session_json(tmp_path: Path):
    path = tmp_path / "empty.zip"
    session_zip(b"{}", path, folder="a/b")  # session.json two folders down is not the session
    truth = listed_truth(tmp_path, path)
    with pytest.raises(InputError, match=r"expected exactly one session\.json"):
        import_session(path, MAP, RULES, truth, tmp_path / "out.json", decide_outcomes=False)


def test_not_a_zip(tmp_path: Path):
    path = tmp_path / "session.zip"
    path.write_text("not a zip")
    truth = listed_truth(tmp_path, path)
    with pytest.raises(InputError, match="not a zip file"):
        import_session(path, MAP, RULES, truth, tmp_path / "out.json", decide_outcomes=False)


def test_capture_must_be_in_the_survey(tmp_path: Path, monkeypatch):
    session = copy.deepcopy(committed_session())
    session["session"]["appVersion"] = "changed"  # different bytes, different sha256
    path = session_zip(session, tmp_path / "session.zip")
    capture = hashlib.sha256(path.read_bytes()).hexdigest()

    def no_unpacking(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("an unlisted zip was opened")

    # The listing is checked before the zip is opened, so nothing in it is decompressed.
    monkeypatch.setattr(zipfile, "ZipFile", no_unpacking)
    with pytest.raises(InputError, match=f"captures does not list {capture}"):
        import_session(path, MAP, RULES, TRUTH, tmp_path / "out.json", decide_outcomes=False)


def test_end_to_end_scores_and_decide_row_lands_in_checks(tmp_path: Path, capsys):
    outputs = []
    for flag in ([], ["--decide"]):
        out = tmp_path / f"measure-lab{'-rule' if flag else ''}.json"
        args = [str(SESSION_ZIP), "--map", str(MAP), "--rules", str(RULES), "--truth", str(TRUTH)]
        assert main(["import-measure-lab", *args, *flag, "--out", str(out)]) == 0
        outputs.append(out)
    assert "wrote" in capsys.readouterr().err
    load_study(RULES, [TRUTH], outputs)  # both rows load without errors

    csv_dir = tmp_path / "csv"
    args = ["--rules", str(RULES), "--truth", str(TRUTH), "--out", str(csv_dir)]
    assert main([*args, "--results", *map(str, outputs)]) == 0
    stdout = capsys.readouterr().out
    assert (
        "| `measure-lab` | ar_poses | 3/9 | 1.20 | 1.20 | 3/3 | 2 | 2 | 0 | 0 | 1 | 1 |" in stdout
    )
    assert "| `measure-lab+rule` | 5/7 | 0 | 0 | 1 | 0 | 0 | 5 | 0 | 0 |" in stdout
    assert "| `measure-lab` | no decisions |" in stdout

    with (csv_dir / "checks.csv").open(newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r["pipeline"] == "measure-lab+rule"]
    assert len(rows) == 8
    route = {r["candidate"]: r for r in rows if r["check"] == "route"}
    # An unsupported route is a justified unsure: over-cautious at c1, where the survey passes, and
    # correct at c2, where the survey is review.
    assert [route["c1"][k] for k in ("truth_outcome", "run_outcome", "over_caution")] == [
        "pass",
        "unsure",
        "true",
    ]
    assert [route["c2"][k] for k in ("truth_outcome", "run_outcome", "agrees")] == [
        "review",
        "unsure",
        "true",
    ]
    assert route["c1"]["abstention"] == route["c2"]["abstention"] == "justified"


def test_cli_reports_errors_with_exit_2(tmp_path: Path, capsys):
    args = [str(SESSION_ZIP), "--map", str(tmp_path / "nope.json"), "--rules", str(RULES)]
    code = main(["import-measure-lab", *args, "--truth", str(TRUTH), "--out", str(tmp_path / "o")])
    assert code == 2
    assert "score import-measure-lab:" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


# The committed session must hold only what Measure Lab's exporter at 43fda59 can write
# (measuredValues and measurementWarnings in MeasureGeometry, addMeasurement in LabSession).
POINT_TO_POINT = measure_lab.POINT_TO_POINT_KEYS
POINT_TO_WALL = measure_lab.POINT_TO_WALL_KEYS
SESSION = committed_session()
WALL_BY_ID = {wall["id"]: wall for wall in SESSION["walls"]}
WALLS = frozenset(WALL_BY_ID)
POINTS = {point["id"]: point for point in SESSION["points"]}
WALL_WARNINGS = frozenset(warning for wall in SESSION["walls"] for warning in wall["warnings"])


class TestFixtureMatchesTheExporter:
    @pytest.mark.parametrize("measurement", SESSION["measurements"], ids=lambda m: m["id"])
    def test_quantity_set_follows_the_endpoints(self, measurement):
        assert measurement["from"] in POINTS
        if measurement["to"] in WALLS:
            # Point to wall: facing gap and height above ground only, and no reference wall.
            assert set(measurement["values"]) == POINT_TO_WALL
            assert measurement["referenceWall"] is None
        else:
            # Point to point: straight, horizontal and vertical, plus alongWall exactly when a
            # reference wall is recorded.
            assert measurement["to"] in POINTS
            expected = set(POINT_TO_POINT)
            if measurement["referenceWall"] is not None:
                assert measurement["referenceWall"] in WALLS
                expected.add("alongWall")
            assert set(measurement["values"]) == expected

    @pytest.mark.parametrize("measurement", SESSION["measurements"], ids=lambda m: m["id"])
    def test_compared_is_emitted_and_accepted_means_no_warnings(self, measurement):
        values = measurement["values"]
        assert measurement["compared"] in values
        assert measurement["accepted"] == (measurement["warnings"] == [])
        below = measurement["compared"] == "heightAboveGround" and values["heightAboveGround"] < 0
        assert ("belowGround" in measurement["warnings"]) == below
        for key, value in values.items():
            assert key == "heightAboveGround" or value >= 0

    @pytest.mark.parametrize("measurement", SESSION["measurements"], ids=lambda m: m["id"])
    def test_warnings_come_from_the_endpoints(self, measurement):
        endpoints = [POINTS[measurement["from"]], POINTS.get(measurement["to"])]
        inherited = {
            flag for point in endpoints if point for flag in point["flags"] + point["wallWarnings"]
        }
        assert set(measurement["warnings"]) - {"belowGround"} <= inherited | WALL_WARNINGS

    @pytest.mark.parametrize("measurement", SESSION["measurements"], ids=lambda m: m["id"])
    def test_values_follow_from_the_coordinates(self, measurement):
        # Recompute each value from point positions and the wall, as MeasureGeometry does.
        a = POINTS[measurement["from"]]["position"]
        if measurement["to"] in WALLS:
            wall = WALL_BY_ID[measurement["to"]]
            start, direction, normal = wall["start"], wall["direction"], wall["normal"]
            rel = [a[i] - start[i] for i in range(3)]
            along = sum(rel[i] * direction[i] for i in range(3))
            ground = start[1] + (wall["end"][1] - start[1]) * along / wall["length"]
            expected = {
                "gapToWall": abs(sum(rel[i] * normal[i] for i in range(3))),
                "heightAboveGround": a[1] - ground,
            }
        else:
            b = POINTS[measurement["to"]]["position"]
            d = [b[i] - a[i] for i in range(3)]
            expected = {
                "straight": math.hypot(*d),
                "horizontal": math.hypot(d[0], d[2]),
                "vertical": abs(d[1]),
            }
            if measurement["referenceWall"] is not None:
                direction = WALL_BY_ID[measurement["referenceWall"]]["direction"]
                expected["alongWall"] = abs(sum(d[i] * direction[i] for i in range(3)))
        for key, value in measurement["values"].items():
            assert value == pytest.approx(expected[key], abs=1e-4), key

    def test_refusals_name_a_real_tool(self):
        tools = {"ground", "wall", "wallPoint", "twoView"}
        assert {refusal["tool"] for refusal in SESSION["refusals"]} <= tools


def record_reads(monkeypatch, path: Path) -> tuple[list[int], list[int]]:
    """Record each read's requested size and returned length on `path`, and fail on read_bytes."""
    sizes: list[int] = []
    lengths: list[int] = []
    real_open = Path.open

    def recording_open(self: Path, *args: Any, **kwargs: Any):
        handle = real_open(self, *args, **kwargs)
        if self != path:
            return handle
        real_read = handle.read

        def read(size: int = -1) -> bytes:
            sizes.append(size)
            data = real_read(size)
            lengths.append(len(data))
            return data

        handle.read = read
        return handle

    def no_read_bytes(self: Path) -> bytes:
        raise AssertionError(f"{self} was read whole")

    monkeypatch.setattr(Path, "open", recording_open)
    monkeypatch.setattr(Path, "read_bytes", no_read_bytes)
    return sizes, lengths


def test_session_zip_is_hashed_in_bounded_chunks(tmp_path: Path, monkeypatch):
    path = session_zip(committed_session(), tmp_path / "session.zip")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    truth = dataclasses.replace(load_truth(TRUTH, load_rules(RULES)), captures=(expected,))
    sizes, _ = record_reads(monkeypatch, path)
    assert sha256_file(path) == expected
    assert load_session(path, truth).zip_sha256 == expected
    assert sizes and all(size == HASH_CHUNK_BYTES for size in sizes)


def test_hash_accumulates_across_chunks(tmp_path: Path, monkeypatch):
    # Two full chunks and a 17-byte tail, so the digest must combine three reads.
    data = bytes(i % 251 for i in range(2 * HASH_CHUNK_BYTES + 17))
    path = tmp_path / "large.bin"
    path.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    sizes, lengths = record_reads(monkeypatch, path)
    assert sha256_file(path) == expected
    assert sizes == [HASH_CHUNK_BYTES] * 4
    assert lengths == [HASH_CHUNK_BYTES, HASH_CHUNK_BYTES, 17, 0]


INPUT_ROLES = ("session zip", "map", "rules", "truth")


class TestOutputNeverOverwritesAnInput:
    def inputs(self, root: Path) -> list[Path]:
        paths = [root / name for name in ("session.zip", "map.json", "rules.json", "truth.json")]
        paths[0].write_bytes(SESSION_ZIP.read_bytes())
        paths[1].write_bytes(MAP.read_bytes())
        paths[2].write_bytes(RULES.read_bytes())
        paths[3].write_bytes(TRUTH.read_bytes())
        return paths

    def check_refused(self, paths: list[Path], out: Path, role: str) -> None:
        before = [path.read_bytes() for path in paths]
        with pytest.raises(InputError, match=f"--out .* is the {role} "):
            import_session(*paths, out, decide_outcomes=False)
        assert [path.read_bytes() for path in paths] == before

    @pytest.mark.parametrize("role", INPUT_ROLES)
    def test_same_path(self, tmp_path: Path, role: str):
        paths = self.inputs(tmp_path)
        self.check_refused(paths, paths[INPUT_ROLES.index(role)], role)

    @pytest.mark.parametrize("role", INPUT_ROLES)
    def test_other_spelling_of_the_same_path(self, tmp_path: Path, role: str):
        paths = self.inputs(tmp_path)
        target = paths[INPUT_ROLES.index(role)]
        (tmp_path / "sub").mkdir()
        self.check_refused(paths, tmp_path / "sub" / ".." / target.name, role)

    @pytest.mark.parametrize("role", INPUT_ROLES)
    def test_symlink(self, tmp_path: Path, role: str):
        paths = self.inputs(tmp_path)
        link = tmp_path / "link.json"
        link.symlink_to(paths[INPUT_ROLES.index(role)])
        self.check_refused(paths, link, role)

    @pytest.mark.parametrize("role", INPUT_ROLES)
    def test_hard_link(self, tmp_path: Path, role: str):
        paths = self.inputs(tmp_path)
        link = tmp_path / "hard.json"
        os.link(paths[INPUT_ROLES.index(role)], link)
        self.check_refused(paths, link, role)

    def test_cli_exit_2_and_inputs_intact(self, tmp_path: Path, capsys):
        paths = self.inputs(tmp_path)
        before = paths[1].read_bytes()
        args = [str(paths[0]), "--map", str(paths[1]), "--rules", str(paths[2])]
        code = main(["import-measure-lab", *args, "--truth", str(paths[3]), "--out", str(paths[1])])
        assert code == 2
        assert "would overwrite an input" in capsys.readouterr().err
        assert paths[1].read_bytes() == before
