"""The burst8 supported-case packet: independently authored source/wire pairs.

The packet at ``server/placement/tests/data/capture_cases_v1`` is a separate
suite from the W7 default manifest. Its positives are hand-authored twice —
once in metres (source) and once in feet (wire), derived by hand rather than
by running any converter — so the pair comparison below is a real check of the
W7 bridge, and its negatives pin the bridge's documented refusals, including
three cases that preserve W7's limitations byte-for-byte or byte-derived.

These tests reuse the manifest loader and suite runner (hash pinning, input
hashes, expectation evaluation) and add what the default suite does not do:
comparing each positive source's bridge conversion against the authored wire
scene, and running the solver over both byte streams and requiring identical
responses apart from the request-identity fields.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from server.placement import case_suite as cs
from server.placement.scene_bridge import SceneBridgeError, convert_scene

PLACEMENT_DATA = Path(__file__).parents[1] / "server" / "placement" / "tests" / "data"
PACKET_DIR = PLACEMENT_DATA / "capture_cases_v1"
MANIFEST = PACKET_DIR / "manifest.json"

POSITIVE_IDS = ("hazard-right-ground-gap", "hazard-left-ground-gap")
NEGATIVE_IDS = (
    "multiwall-source-unsupported",
    "generated-object-evidence-unsupported",
    "standoff-meter-anchor-unsupported",
    "footprint-hazard-unsupported",
    "generated-regions-unsupported",
)

# The wire scenes round metres-derived feet to six decimals; the bridge emits
# full float precision. The largest possible gap is half of the last kept
# decimal, 5e-7 ft — about 0.15 micrometres, far inside any survey tolerance.
WIRE_ROUNDING_TOLERANCE_FT = 5e-7


def _payload() -> dict:
    return json.loads(MANIFEST.read_text())


def _case(payload: dict, case_id: str) -> dict:
    return next(case for case in payload["cases"] if case["id"] == case_id)


def _positive_docs(case_id: str) -> tuple[dict, dict]:
    case = _case(_payload(), case_id)
    source = json.loads((PACKET_DIR / case["source"]["path"]).read_text())
    wire = json.loads((PACKET_DIR / case["wire"]["path"]).read_text())
    return source, wire


@pytest.mark.parametrize("case_id", POSITIVE_IDS)
def test_positive_source_is_schema_valid(case_id: str) -> None:
    source, _ = _positive_docs(case_id)
    validator = cs._schema_validator(cs.SOURCE_SCHEMA)
    assert cs._schema_errors(validator, source) == []


def _differences(converted: dict, wire: dict, path: str = "", tolerance: float | None = None) -> list[str]:
    """Structural diffs first, then float gaps within the declared tolerance."""
    if tolerance is None:
        tolerance = WIRE_ROUNDING_TOLERANCE_FT
    if isinstance(converted, dict) and isinstance(wire, dict):
        diffs = []
        for key in sorted(set(converted) | set(wire)):
            if key not in converted or key not in wire:
                # The bridge omits empty lists; the wire writes [] (declared).
                if wire.get(key) == [] and converted.get(key) is None:
                    continue
                diffs.append(f"{path}/{key}: key present on one side only")
            else:
                diffs.extend(_differences(converted[key], wire[key], f"{path}/{key}", tolerance))
        return diffs
    if isinstance(converted, list) and isinstance(wire, list):
        if len(converted) != len(wire):
            return [f"{path}: list length {len(converted)} != {len(wire)}"]
        diffs = []
        for index, (left, right) in enumerate(zip(converted, wire, strict=False)):
            diffs.extend(_differences(left, right, f"{path}[{index}]", tolerance))
        return diffs
    if (
        isinstance(converted, (int, float))
        and isinstance(wire, (int, float))
        and not isinstance(converted, bool)
        and not isinstance(wire, bool)
    ):
        if abs(converted - wire) > tolerance:
            return [f"{path}: {converted} != {wire} beyond {tolerance}"]
        return []
    if converted != wire:
        return [f"{path}: {converted!r} != {wire!r}"]
    return []


@pytest.mark.parametrize("case_id", POSITIVE_IDS)
def test_positive_bridge_conversion_matches_authored_wire(case_id: str) -> None:
    source, wire = _positive_docs(case_id)
    converted = convert_scene(source)
    wire_without_envelope = {key: value for key, value in wire.items() if key != "schema_version"}
    diffs = _differences(converted, wire_without_envelope)
    assert diffs == []


@pytest.fixture(scope="module")
def packet_report() -> dict:
    return cs.run_suite(MANIFEST)


def test_packet_summary(packet_report: dict) -> None:
    assert packet_report["schema_version"] == cs.REPORT_SCHEMA_VERSION
    assert packet_report["summary"] == {
        "cases": 7,
        "positive": 2,
        "negative_source": 5,
        "matched": 2,
        "mismatched": 0,
        "awaiting_conversion": 5,
        "errored": 0,
    }
    assert packet_report["ok"] is True


@pytest.mark.parametrize("case_id", POSITIVE_IDS)
def test_positive_case_solved_and_matched(packet_report: dict, case_id: str) -> None:
    case = next(case for case in packet_report["cases"] if case["id"] == case_id)
    assert case["status"] == cs.STATUS_MATCHED, case
    assert case["executed"] is True
    assert case["executions_agree"] is True
    assert case["input_sha_matches_file"] is True
    assert case["decision"] == "manual_review"


def test_solver_agrees_between_wire_and_converted_bytes() -> None:
    """The wire scene and the bridge's conversion of the source must produce
    the same solver response apart from request identity."""
    client = cs._get_client()
    for case_id in POSITIVE_IDS:
        case = _case(_payload(), case_id)
        source_bytes = (PACKET_DIR / case["source"]["path"]).read_bytes()
        wire_bytes = (PACKET_DIR / case["wire"]["path"]).read_bytes()
        wire_body = client.post("/v1/placements", content=wire_bytes, headers={"Content-Type": "application/json"})
        assert wire_body.status_code == 200
        converted = convert_scene(json.loads(source_bytes))
        converted_bytes = (json.dumps({"schema_version": "1.0", **converted}, indent=2) + "\n").encode()
        source_body = client.post(
            "/v1/placements", content=converted_bytes, headers={"Content-Type": "application/json"}
        )
        assert source_body.status_code == 200

        def identity(body: dict) -> dict:
            scrubbed = copy.deepcopy(body)
            scrubbed["stats"].pop("elapsed_ms", None)
            scrubbed["stats"].pop("input_sha256", None)
            scrubbed.pop("input_sha256", None)
            return scrubbed

        # The wire's six-decimal rounding propagates at most a few 1e-6 ft into
        # derived solver outputs; any real geometry disagreement would move a
        # candidate by the 1/96 ft sweep step. 1e-4 ft (0.03 mm) separates them.
        diffs = _differences(identity(wire_body.json()), identity(source_body.json()), tolerance=1e-4)
        assert diffs == [], (case_id, diffs)


@pytest.mark.parametrize("case_id", NEGATIVE_IDS)
def test_negative_refuses_where_declared(case_id: str) -> None:
    case = _case(_payload(), case_id)
    source = json.loads((PACKET_DIR / case["source"]["path"]).read_text())
    with pytest.raises(SceneBridgeError) as excinfo:
        convert_scene(source)
    assert excinfo.value.code == case["expected_error"]["code"], case_id
    assert excinfo.value.pointer == case["expected_error"]["pointer"], case_id


def test_w7_preserved_copies_are_byte_identical() -> None:
    """The two byte-preserved W7 limitation sources must match the W7 fixtures
    they document, hash for hash."""
    w7_dir = PLACEMENT_DATA / "cases"
    for packet_name, w7_name in (
        ("standoff-meter-anchor-unsupported.source.json", "hazard-right-ground-gap.source.json"),
        ("generated-regions-unsupported.source.json", "generated-hazard-unsupported.source.json"),
    ):
        packet_bytes = (PACKET_DIR / packet_name).read_bytes()
        w7_bytes = (w7_dir / w7_name).read_bytes()
        assert packet_bytes == w7_bytes, packet_name


def test_tampered_spot_expectation_is_caught(tmp_path: Path) -> None:
    payload = _packet_payload_absolutized()
    _case(payload, POSITIVE_IDS[0])["expectations"]["spot"]["span_ft"] = [0.0, 1.0]
    drift = tmp_path / "manifest.json"
    drift.write_text(json.dumps(payload))
    report = cs.run_suite(drift)
    case = next(item for item in report["cases"] if item["id"] == POSITIVE_IDS[0])
    assert case["status"] == cs.STATUS_MISMATCHED
    assert any("spot" in mismatch for mismatch in case["mismatches"])
    assert report["ok"] is False


def test_drifted_pair_wire_fails_with_path(tmp_path: Path) -> None:
    payload = _packet_payload_absolutized()
    case = _case(payload, POSITIVE_IDS[0])
    fixture = tmp_path / "drifted.wire.json"
    fixture.write_bytes(json.dumps(json.loads((PACKET_DIR / case["wire"]["path"]).read_text())).encode() + b" ")
    case["wire"]["sha256"] = "0" * 64
    case["wire"]["path"] = str(fixture)
    drift = tmp_path / "manifest.json"
    drift.write_text(json.dumps(payload))
    with pytest.raises(cs.CaseSuiteError) as excinfo:
        cs.load_cases(drift)
    assert str(fixture) in str(excinfo.value)


def _packet_payload_absolutized() -> dict:
    """Packet manifest with fixture paths made absolute so a copy anywhere
    still resolves the committed fixtures."""
    payload = json.loads(MANIFEST.read_text())
    for case in payload["cases"]:
        case["source"]["path"] = str(PACKET_DIR / case["source"]["path"])
        if case.get("wire"):
            case["wire"]["path"] = str(PACKET_DIR / case["wire"]["path"])
    return payload
