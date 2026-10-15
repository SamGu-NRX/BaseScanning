"""Round trips, schema equivalence, and the manifest checks other workstreams import."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from server.contracts._base import canonical_json, dump_json
from server.contracts.api import ConfirmationData, ConfirmationRequest, CreateCaptureRequest, HintData, RegisterFile
from server.contracts.export_schemas import main as export_main
from server.contracts.labels import Labels, labels_consistency_errors
from server.contracts.packet import FileEntry, Packet
from server.contracts.result import ResultResponse
from server.contracts.scene import Scene, scene_consistency_errors
from server.contracts.validation import check_packet
from server.contracts.verdict import Verdict, verdict_contract_errors

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = ROOT / "server/contracts/schemas"
DATA = Path(__file__).resolve().parent / "data"
NEG = Path(__file__).resolve().parent / "negatives"
EXAMPLES = ROOT / "data/fixtures/packets/examples"

_PACKET = json.loads((SCHEMA_DIR / "capture-packet.v0.4.schema.json").read_text())
_SCENE = json.loads((SCHEMA_DIR / "scene.v0.schema.json").read_text())
_VERDICT = json.loads((SCHEMA_DIR / "verdict.v0.schema.json").read_text())
_LABELS = json.loads((SCHEMA_DIR / "labels.v0.schema.json").read_text())
_RESULT = json.loads((SCHEMA_DIR / "result.v0.schema.json").read_text())
_API = json.loads((SCHEMA_DIR / "api.v1.schema.json").read_text())

_MODELS = {
    "house-capture-packet": (Packet, _PACKET),
    "base-scene/v0": (Scene, _SCENE),
    "base-verdict/v0": (Verdict, _VERDICT),
    "base-labels/v0": (Labels, _LABELS),
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _model_ok(model: type, document: dict) -> bool:
    try:
        model.model_validate(document)
    except ValidationError:
        return False
    return True


@pytest.mark.parametrize(
    "path",
    [
        EXAMPLES / "example-packet.json",
        EXAMPLES / "example-packet-photo-only.json",
        DATA / "minimal-scene.json",
        DATA / "minimal-verdict.json",
        DATA / "minimal-labels.json",
    ],
)
def test_round_trip(path: Path) -> None:
    document = _load(path)
    model, _schema = _MODELS[document["format"]]
    parsed = model.model_validate(document)
    assert canonical_json(dump_json(parsed)) == canonical_json(document)


@pytest.mark.parametrize(
    "path",
    [
        EXAMPLES / "example-packet.json",
        EXAMPLES / "example-packet-photo-only.json",
        DATA / "minimal-scene.json",
        DATA / "minimal-verdict.json",
        DATA / "minimal-labels.json",
        *sorted(NEG.glob("*.json")),
    ],
)
def test_schema_equivalence(path: Path) -> None:
    document = _load(path)
    fmt = document.get("format")
    if fmt in _MODELS:
        model, schema = _MODELS[fmt]
    elif "outcome" in document and "memberActions" in document:
        model, schema = ResultResponse, _RESULT
    elif "flow" in document and "packetId" in document:
        model, schema = CreateCaptureRequest, _API
    else:
        raise AssertionError(f"no model for {path.name}")
    schema_ok = Draft202012Validator(schema).is_valid(document)
    model_ok = _model_ok(model, document)
    assert schema_ok == model_ok, path.name


def test_example_manifest_has_no_errors() -> None:
    packet = Packet.model_validate(_load(EXAMPLES / "example-packet.json"))
    report = check_packet(packet)
    assert report.ok
    assert report.errors == []
    assert report.stage == "manifest"
    assert report.imagesDecodable == 0
    assert report.to_api_error().errors == []


def test_unknown_tap_keyframe() -> None:
    document = _load(EXAMPLES / "example-packet.json")
    document["taps"][0]["keyframe"] = "missing"
    packet = Packet.model_validate(document)
    report = check_packet(packet)
    assert [item.code for item in report.errors] == ["tap_unknown_keyframe"]
    assert report.errors[0].pointer == "/taps/0/keyframe"


def test_scene_anchor_must_be_meter() -> None:
    document = _load(NEG / "scene-anchor-window.json")
    assert scene_consistency_errors(document)


def test_scene_chain_wall_must_exist() -> None:
    document = _load(NEG / "scene-unknown-chain-wall.json")
    assert any("unknown wall" in item for item in scene_consistency_errors(document))


def test_verdict_pass_needs_observed_evidence() -> None:
    grazing = _load(NEG / "verdict-pass-on-grazing.json")
    empty = _load(NEG / "verdict-pass-without-evidence.json")
    assert verdict_contract_errors(grazing)
    assert verdict_contract_errors(empty)
    assert not verdict_contract_errors(_load(DATA / "minimal-verdict.json"))


def test_start_range_label_needs_span() -> None:
    document = _load(NEG / "label-start-range-missing-span.json")
    assert labels_consistency_errors(document)


def test_keyframe_image_role_rejected() -> None:
    raw = {
        "path": "keyframes/k1.jpg",
        "bytes": 1,
        "sha256": "a" * 64,
        "role": "keyframe_image",
    }
    with pytest.raises(ValidationError, match="role"):
        FileEntry.model_validate(raw)


def test_tier_rule_message() -> None:
    document = _load(NEG / "photo-only-with-keyframe.json")
    with pytest.raises(ValidationError, match="photo_only packets must have no keyframes"):
        Packet.model_validate(document)


def test_hint_confirm_matches_sd51() -> None:
    prompt = {
        "imageId": "s1",
        "fieldId": "meter_number",
        "mode": "pick",
        "candidates": ["123"],
        "preselected": "123",
        "allowNone": True,
    }
    parsed = HintData.model_validate(
        {"code": "meter_number_confirm", "message": "Check the meter number", "confirm": prompt}
    )
    assert parsed.confirm is not None
    assert parsed.confirm.mode == "pick"
    with pytest.raises(ValidationError, match="meter_number_confirm"):
        HintData.model_validate({"code": "meter_plate_unreadable", "message": "Retake", "confirm": prompt})
    with pytest.raises(ValidationError):
        HintData.model_validate(
            {
                "code": "meter_number_confirm",
                "message": "Check",
                "confirm": {**prompt, "mode": "prefill"},
            }
        )


def test_confirmation_data_carries_by() -> None:
    parsed = ConfirmationData.model_validate(
        {
            "confirmationId": "cfm_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "fieldId": "meter_number",
            "imageId": "s1",
            "kind": "confirmed",
            "by": "device",
        }
    )
    assert parsed.by == "device"
    with pytest.raises(ValidationError, match="by"):
        ConfirmationData.model_validate(
            {
                "confirmationId": "cfm_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                "fieldId": "meter_number",
                "imageId": "s1",
                "kind": "confirmed",
            }
        )
    defs = _API["$defs"]
    assert defs["HintData"]["properties"]["confirm"]
    assert defs["ConfirmationData"]["properties"]["by"]["enum"] == ["device", "reviewer"]
    assert "by" in defs["ConfirmationResponse"]["required"]


def _definition(schema: dict, name: str) -> dict:
    return {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{name}"}


def test_footprint_points_are_pairs() -> None:
    document = _load(DATA / "minimal-verdict.json")
    document["candidates"][0]["footprint_m"] = [[0.0, 1.0, 2.0]]
    assert not Draft202012Validator(_VERDICT).is_valid(document)
    with pytest.raises(ValidationError):
        Verdict.model_validate(document)
    document["candidates"][0]["footprint_m"] = [[0.0]]
    assert not Draft202012Validator(_VERDICT).is_valid(document)
    with pytest.raises(ValidationError):
        Verdict.model_validate(document)
    document["candidates"][0]["footprint_m"] = [[0.0, 1.0]]
    assert Draft202012Validator(_VERDICT).is_valid(document)
    Verdict.model_validate(document)


def test_register_file_constraints_are_in_the_schema() -> None:
    schema = _definition(_API, "RegisterFile")
    good = {
        "path": "files/a.jpg",
        "bytes": 1,
        "sha256": "a" * 64,
        "md5": "d41d8cd98f00b204e9800998ecf8427e",
        "role": "still",
        "contentType": "image/jpeg",
    }
    bad = {**good, "path": "../../etc/passwd", "sha256": "abc"}
    assert Draft202012Validator(schema).is_valid(good)
    RegisterFile.model_validate(good)
    assert not Draft202012Validator(schema).is_valid(bad)
    with pytest.raises(ValidationError):
        RegisterFile.model_validate(bad)


def test_confirmation_value_required_unless_none() -> None:
    schema = _definition(_API, "ConfirmationRequest")
    base = {
        "fieldId": "meter_number",
        "imageId": "s1",
        "clientAt": "2026-09-26T12:00:00Z",
    }
    missing = {**base, "kind": "confirmed"}
    nulled = {**base, "kind": "confirmed", "value": None}
    declined = {**base, "kind": "none"}
    given = {**base, "kind": "confirmed", "value": "A1"}
    assert not Draft202012Validator(schema).is_valid(missing)
    assert not Draft202012Validator(schema).is_valid(nulled)
    with pytest.raises(ValidationError, match="value_required"):
        ConfirmationRequest.model_validate(missing)
    with pytest.raises(ValidationError, match="value_required"):
        ConfirmationRequest.model_validate(nulled)
    assert Draft202012Validator(schema).is_valid(declined)
    ConfirmationRequest.model_validate(declined)
    assert Draft202012Validator(schema).is_valid(given)
    ConfirmationRequest.model_validate(given)


def test_exported_schemas_match_committed_files() -> None:
    assert export_main(["--check"]) == 0


def test_copied_schema_hashes() -> None:
    text = (SCHEMA_DIR / "SOURCES.md").read_text()
    for name in (
        "scene.v0.schema.json",
        "verdict.v0.schema.json",
        "labels.v0.schema.json",
        "capture-packet.v0.4.schema.json",
    ):
        digest = hashlib.sha256((SCHEMA_DIR / name).read_bytes()).hexdigest()
        assert digest in text
