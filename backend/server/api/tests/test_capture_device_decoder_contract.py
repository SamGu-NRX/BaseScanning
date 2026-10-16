"""Every body the app reads decodes the way the Swift client decodes it.

The iOS client decodes the capture API with plain `Codable` (`CaptureAPI.swift` for
create/register/commit/finalize/events/errors, `CaptureResult.swift` for the result
envelope). Codable is all-or-nothing: one wrong value anywhere in a body makes the
whole body unreadable, and the uploader ends the scan `answerUnreadable`. PR #257
pinned the upload-side key names; nothing pinned the result envelope, the event data
fields, the terminal/outcome-less status sets, or the error shape the app's
`CaptureAPI.errorCodes` reads.

This file carries a small Python mirror of Swift Codable decoding for exactly the
structs the app declares — required vs optional keys, `Int` that accepts an integer
literal or an exactly integral number such as `1.0`, `Double` that must be a finite JSON
number, unknown enum strings decoding
as `unknown(...)` instead of failing, unknown keys ignored — and then runs every
body the server actually serves through it: create, register, PUT, commit,
finalize, events (including a long-poll that times out empty), result for each
terminal status reachable here, review/preview refusals, and every error body the
walk produces. The Swift sources are the client contract; the server is only
changed where `backend/docs/INTEGRATION.md` or the published schemas already
promise the app's shape.

Memory state and local volume storage only. No Modal, no GPU, no network.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from server.api.tests.conftest import create_body, md5_b64, photo_packet, sha256_hex

BLOB = b"\xff\xd8\xff\xe9fakejpeg"
PATH = "stills/one.jpg"

# --- the Codable mirror ------------------------------------------------------
#
# Mirrors JSONDecoder's semantics, not the server's schema: `Int` accepts an integer
# literal or an exactly integral number (`1.0`, `1e0` — Swift's JSONDecoder round-trips
# these through NSNumber.intValue), while a fractional number, a bool, or a string
# fails; `Double` accepts any finite JSON number, `Bool` is never a
# number, unknown keys are ignored, and an enum with an `unknown(String)` case
# decodes any string. Null passes only where the Swift property is Optional; a
# missing key passes only where it is Optional.


class DecodeFailure(AssertionError):
    def __init__(self, path: str, why: str) -> None:
        super().__init__(f"{path}: {why}")
        self.path = path
        self.why = why


def _reject_constant(token: str) -> float:
    raise ValueError(f"{token} is not valid JSON for Swift's JSONDecoder")


def wire_loads(body: bytes) -> Any:
    """json.loads the way NSJSONSerialization must: no NaN/Infinity tokens."""

    return json.loads(body, parse_constant=_reject_constant)


def as_string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise DecodeFailure(path, f"Swift String expected, got {type(value).__name__}")
    return value


def as_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DecodeFailure(path, "Swift Int expects an integer literal; a float or bool fails")
    if not -(2**63) <= value < 2**63:
        raise DecodeFailure(path, "integer does not fit in Int64")
    return value


def as_double(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecodeFailure(path, f"Swift Double expected a JSON number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise DecodeFailure(path, "Swift Double requires a finite number")
    return number


def as_string_array(value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        raise DecodeFailure(path, f"Swift [String] expected, got {type(value).__name__}")
    return [as_string(item, f"{path}[{index}]") for index, item in enumerate(value)]


def as_string_map(value: Any, path: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise DecodeFailure(path, f"Swift [String: String] expected, got {type(value).__name__}")
    return {key: as_string(item, f"{path}.{key}") for key, item in value.items()}


def optional(inner: Callable[[Any, str], Any]) -> Callable[[Any, str], Any | None]:
    def decode(value: Any, path: str) -> Any | None:
        return None if value is None else inner(value, path)

    return decode


def struct_of(fields: dict[str, tuple[Callable[[Any, str], Any], bool]]) -> Callable[[Any, str], dict[str, Any]]:
    """fields maps the wire key to (decoder, required). Unknown keys are ignored."""

    def decode(value: Any, path: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise DecodeFailure(path, f"Swift struct expected an object, got {type(value).__name__}")
        out: dict[str, Any] = {}
        for key, (inner, required) in fields.items():
            if key in value:
                out[key] = inner(value[key], f"{path}.{key}")
            elif required:
                raise DecodeFailure(f"{path}.{key}", "required key is missing (Swift keyNotFound)")
            else:
                out[key] = None  # decodeIfPresent: an absent optional decodes as nil
        return out

    return decode


def array_of(inner: Callable[[Any, str], Any]) -> Callable[[Any, str], list[Any]]:
    def decode(value: Any, path: str) -> list[Any]:
        if not isinstance(value, list):
            raise DecodeFailure(path, f"Swift [T] expected, got {type(value).__name__}")
        return [inner(item, f"{path}[{index}]") for index, item in enumerate(value)]

    return decode


STR = as_string
INT = as_int
DBL = as_double
STR_OPT = optional(as_string)
INT_OPT = optional(as_int)
DBL_OPT = optional(as_double)
STR_ARR = as_string_array

# CaptureAPI.Limits — maxSinglePutBytes/urlTtlS/maxFiles/maxBatch, all Int?.
LIMITS = struct_of(
    {
        "maxSinglePutBytes": (INT_OPT, False),
        "urlTtlS": (INT_OPT, False),
        "maxFiles": (INT_OPT, False),
        "maxBatch": (INT_OPT, False),
    }
)

# CaptureAPI.CreateResponse — captureId and status required; upload/finalizeBy optional.
CREATE_RESPONSE = struct_of(
    {
        "captureId": (STR, True),
        "status": (STR, True),  # Status enum with unknown(String): any string decodes
        "upload": (optional(LIMITS), False),
        "finalizeBy": (STR_OPT, False),
    }
)

# CaptureAPI.UploadTarget — method required; url/headers/expiresAt optional.
UPLOAD_TARGET = struct_of(
    {
        "method": (STR, True),
        "url": (STR_OPT, False),
        "headers": (optional(as_string_map), False),
        "expiresAt": (STR_OPT, False),
    }
)

# CaptureAPI.RegisteredFile / RegisterResponse.
REGISTERED_FILE = struct_of({"path": (STR, True), "state": (STR, True), "upload": (optional(UPLOAD_TARGET), False)})
REGISTER_RESPONSE = struct_of({"files": (array_of(REGISTERED_FILE), True)})

# CaptureAPI.CommitResponse — all three arrays required.
COMMIT_RESPONSE = struct_of({"committed": (STR_ARR, True), "notFound": (STR_ARR, True), "mismatch": (STR_ARR, True)})

# CaptureAPI.FinalizeResponse — status/missing/runId required, etaS Int?.
FINALIZE_RESPONSE = struct_of(
    {
        "status": (STR, True),
        "missing": (STR_ARR, True),
        "runId": (STR, True),
        "etaS": (INT_OPT, False),
    }
)

# CaptureAPI.Event / EventData / EventsResponse. data is Optional; only its six
# declared String? keys can fail the decode — any other key is ignored.
EVENT_DATA = struct_of(
    {
        "code": (STR_OPT, False),
        "kind": (STR_OPT, False),
        "stage": (STR_OPT, False),
        "status": (STR_OPT, False),
        "runId": (STR_OPT, False),
        "next": (STR_OPT, False),
    }
)
EVENT = struct_of(
    {
        "seq": (INT, True),
        "type": (STR, True),  # plain String in Swift: a new server type still decodes
        "at": (STR, True),
        "data": (optional(EVENT_DATA), False),
    }
)
EVENTS_RESPONSE = struct_of({"status": (STR, True), "next": (INT, True), "events": (array_of(EVENT), True)})

# CaptureAPI.ErrorItem / ErrorBody — code required, pointer/message optional.
ERROR_BODY = struct_of(
    {
        "errors": (
            array_of(struct_of({"code": (STR, True), "pointer": (STR_OPT, False), "message": (STR_OPT, False)})),
            True,
        )
    }
)

# CaptureResult.swift, the device result envelope. Enum strings with unknown(...)
# decode any string, so status/outcome kind/criterion outcome mirror as STR.
PROMPT = struct_of({"title": (STR, True), "body": (STR, True)})
REFERENCE_CROP = struct_of({"image": (STR, True), "boxPx": (array_of(DBL), True)})
VIEW_NEEDED = struct_of(
    {
        "id": (STR, True),
        "kind": (STR, True),  # kept as text so a new server kind still shows its prompt
        "criteria": (STR_ARR, True),
        "why": (STR, True),
        "promptId": (STR, True),
        "prompt": (PROMPT, True),
        "band": (STR_OPT, False),
        "wallId": (STR_OPT, False),
        "target": (STR_OPT, False),
        "referenceCrop": (optional(REFERENCE_CROP), False),
    }
)
REASON = struct_of(
    {
        "criterion": (STR, True),
        "text": (STR, True),
        "measuredFt": (DBL_OPT, False),
        "plusMinusFt": (DBL_OPT, False),
        "thresholdFt": (DBL_OPT, False),
        "comparison": (STR_OPT, False),
    }
)
CRITERION = struct_of(
    {
        "id": (STR, True),
        "outcome": (STR, True),
        "unsureCause": (STR_OPT, False),
        "measuredFt": (DBL_OPT, False),
        "plusMinusFt": (DBL_OPT, False),
        "thresholdFt": (DBL_OPT, False),
        "coverage": (STR_OPT, False),
    }
)
BOX_ARKIT_WORLD = struct_of({"pose": (array_of(DBL), True), "size": (array_of(DBL), True)})
RECOMMENDED_PLACEMENT = struct_of(
    {
        "wallId": (STR, True),
        "startSM": (DBL, True),
        "confidence": (DBL, True),
        "boxArkitWorld": (optional(BOX_ARKIT_WORLD), False),
    }
)
OUTCOME = struct_of(
    {
        "kind": (STR, True),
        "profile": (STR, True),
        "message": (STR, True),
        "viewsNeeded": (array_of(VIEW_NEEDED), True),
        "reasons": (array_of(REASON), True),
        "arkitEpoch": (STR_OPT, False),
        "recommendedPlacement": (optional(RECOMMENDED_PLACEMENT), False),
    }
)
RESULT_RESPONSE = struct_of(
    {
        "runId": (STR_OPT, False),
        "status": (STR, True),
        "viewsNeeded": (array_of(VIEW_NEEDED), True),
        "memberActions": (STR_ARR, True),
        "outcome": (optional(OUTCOME), False),
        "criteria": (optional(array_of(CRITERION)), False),
        "previewUrl": (STR_OPT, False),
        "reviewUrl": (STR_OPT, False),
    }
)

# The client's own status sets (CaptureAPI.swift:150-154).
TERMINAL_STATUSES = {"complete", "manual_review", "failed", "expired", "needs_views"}
STATUSES_WITHOUT_OUTCOME = {"failed", "expired"}

# --- helpers -----------------------------------------------------------------


def _anon(client: TestClient) -> TestClient:
    return TestClient(client.app)


def _create(client: TestClient, packet_id: str = "packet-0001") -> dict:
    response = client.post("/v1/captures", json=create_body(packet_id), headers={"Idempotency-Key": packet_id})
    assert response.status_code == 201, response.text
    return response.json()


def _register(
    client: TestClient, capture_id: str, path: str = PATH, data: bytes = BLOB, meta: dict | None = None
) -> dict:
    item: dict[str, Any] = {
        "path": path,
        "bytes": len(data),
        "sha256": sha256_hex(data),
        "md5": md5_b64(data),
        "role": "still",
        "contentType": "image/jpeg",
    }
    if meta is not None:
        item["meta"] = meta
    response = client.post(f"/v1/captures/{capture_id}/files", json={"files": [item]})
    assert response.status_code == 200, response.text
    return response.json()


def _put(client: TestClient, registered: dict, data: bytes = BLOB) -> None:
    target = registered["files"][0]["upload"]
    put = client.put(urlparse(target["url"]).path, content=data, headers=target["headers"])
    assert put.status_code == 200, put.text


def _commit(client: TestClient, capture_id: str, paths: list[str], data: bytes = BLOB) -> dict:
    response = client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": path, "sha256": sha256_hex(data)} for path in paths]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _finalize(client: TestClient, capture_id: str, packet: dict, packet_id: str) -> Any:
    body = json.dumps(packet).encode()
    return client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=body,
        headers={
            "Idempotency-Key": packet_id,
            "Content-Type": "application/json",
            "X-Packet-Sha256": sha256_hex(body),
        },
    )


def _set_status(client: TestClient, capture_id: str, status: str) -> None:
    record = client.app_state.state.get_capture(capture_id)
    assert record is not None
    record.status = status
    client.app_state.state.put_capture(record)


def _seed_ready_result(
    client: TestClient,
    capture_id: str,
    run_id: str,
    stored: dict,
    capture_status: str,
) -> None:
    """A done run whose result blob is on storage — the route's ready path serves it.

    Mirrors `test_result_boundary.py`'s seeding: a run record, a done result stage,
    the blob under `captures/{id}/result/result.json`, and the capture status the
    route overlays onto the served body.
    """

    from server.contracts.jobs import RunRecord, StageRecord

    deps = client.app_state
    result_key = f"captures/{capture_id}/result/result.json"
    deps.storage.put_bytes(result_key, json.dumps(stored).encode(), "application/json")
    deps.state.put_run(
        RunRecord(
            run_id=run_id,
            capture_id=capture_id,
            packet_sha256="a" * 64,
            status="done",
            branch="image",
            flow="photo",
            tier="photo_only",
            profile_default="C",
            created_at="2026-09-26T00:00:00.000Z",
            deadline_at="2026-09-26T01:00:00.000Z",
            artifacts={"result/result.json": result_key},
        )
    )
    deps.state.upsert_stage(
        StageRecord(run_id=run_id, stage="result", status="done", attempt=1, heartbeat="2026-09-26T00:00:00.000Z")
    )
    _set_status(client, capture_id, capture_status)


def decode_served(body: Any, decoder: Callable[[Any, str], Any]) -> dict[str, Any]:
    """Run one served body through the mirror; a failure is a contract disagreement."""

    decoded = decoder(body, "$")
    assert isinstance(decoded, dict)
    return decoded


def decode_error(body: Any, label: str) -> list[str]:
    """Decode an error body the way `CaptureAPI.errorCodes` does."""

    decoded = ERROR_BODY(body, "$")
    codes = [item["code"] for item in decoded["errors"]]
    assert codes, f"{label}: the app would read no error code from {body}"
    return codes


# --- the mirror itself is Swift-faithful (strict, isolated) -------------------


def test_int_mirrors_swift_number_handling() -> None:
    assert as_int(1, "$") == 1
    assert as_int(180, "$") == 180
    # Swift's JSONDecoder: NSNumber(value: 1).intValue == 1 round-trips for 1.0 and 1e0.
    assert as_int(1.0, "$") == 1
    assert as_int(1e0, "$") == 1
    with pytest.raises(DecodeFailure):
        as_int(1.5, "$")  # a fractional number does not round-trip through Int
    with pytest.raises(DecodeFailure):
        as_int(True, "$")  # Bool is never a number
    with pytest.raises(DecodeFailure):
        as_int("180", "$")
    with pytest.raises(DecodeFailure):
        as_int(2.0**63, "$")  # outside Int64 the round-trip fails


def test_double_rejects_bool_string_and_nonfinite() -> None:
    with pytest.raises(DecodeFailure):
        as_double(True, "$")
    with pytest.raises(DecodeFailure):
        as_double("1.2", "$")
    with pytest.raises(ValueError):
        wire_loads(b'{"x": NaN}')  # NSJSONSerialization rejects the token outright
    with pytest.raises(ValueError):
        wire_loads(b'{"x": Infinity}')
    assert as_double(2, "$") == 2.0  # an integer literal decodes as Double


def test_missing_required_key_fails_and_optional_passes() -> None:
    with pytest.raises(DecodeFailure):
        VIEW_NEEDED(
            {
                "id": "v1",
                "kind": "band",
                "criteria": [],
                # "why" missing
                "promptId": "p",
                "prompt": {"title": "t", "body": "b"},
            },
            "$",
        )
    ok = VIEW_NEEDED(
        {
            "id": "v1",
            "kind": "band",
            "criteria": [],
            "why": "w",
            "promptId": "p",
            "prompt": {"title": "t", "body": "b"},
        },
        "$",
    )
    assert ok["band"] is None  # an optional key may be absent
    assert ok["referenceCrop"] is None


def test_null_for_required_key_fails_but_optional_null_passes() -> None:
    with pytest.raises(DecodeFailure):
        OUTCOME(
            {
                "kind": "manual_review",
                "profile": None,  # a null profile is a decode failure, not an absent field
                "message": "m",
                "viewsNeeded": [],
                "reasons": [],
            },
            "$",
        )
    decoded = RESULT_RESPONSE(
        {"runId": None, "status": "uploading", "viewsNeeded": [], "memberActions": [], "outcome": None},
        "$",
    )
    assert decoded["runId"] is None and decoded["outcome"] is None


def test_wrong_containers_fail_and_unknown_keys_are_ignored() -> None:
    with pytest.raises(DecodeFailure):
        EVENT_DATA("capture stuck", "$")  # data must be an object or null
    with pytest.raises(DecodeFailure):
        COMMIT_RESPONSE({"committed": [], "notFound": [], "mismatch": {}}, "$")
    # Unknown keys never fail a Swift decode.
    decoded = EVENT(
        {
            "seq": 1,
            "type": "hint",
            "at": "2026-09-26T00:00:00Z",
            "data": {"code": "x", "message": "homeowner text", "retake": "meter_close"},
            "extra": {"nested": [1, 2, 3]},
        },
        "$",
    )
    assert decoded["data"]["code"] == "x" and "message" not in decoded["data"]


def test_unknown_status_strings_still_decode() -> None:
    """A status or kind the app doesn't know decodes as .unknown, not as an error."""

    decoded = RESULT_RESPONSE(
        {
            "runId": "run_1",
            "status": "quarantined",
            "viewsNeeded": [],
            "memberActions": [],
            "outcome": {
                "kind": "deferred_to_the_cloud",
                "profile": "C",
                "message": "m",
                "viewsNeeded": [],
                "reasons": [],
            },
            "criteria": [{"id": "c", "outcome": "mostly_pass"}],
        },
        "$",
    )
    assert decoded["status"] == "quarantined" and decoded["outcome"]["kind"] == "deferred_to_the_cloud"


# --- the served walk ----------------------------------------------------------


def test_upload_walk_bodies_decode(client: TestClient) -> None:
    packet_id = "packet-0001"
    created = client.post("/v1/captures", json=create_body(packet_id), headers={"Idempotency-Key": packet_id})
    assert created.status_code == 201
    created_decoded = decode_served(created.json(), CREATE_RESPONSE)
    assert created_decoded["status"] == "uploading"
    assert created_decoded["captureId"].startswith("cap_")

    replay = client.post("/v1/captures", json=create_body(packet_id), headers={"Idempotency-Key": packet_id})
    assert replay.status_code == 200
    decode_served(replay.json(), CREATE_RESPONSE)

    capture_id = created.json()["captureId"]
    registered = _register(client, capture_id)
    registered_decoded = decode_served(registered, REGISTER_RESPONSE)
    target = registered_decoded["files"][0]["upload"]
    assert target is not None and target["method"] == "PUT"

    _put(client, registered)
    committed = _commit(client, capture_id, [PATH])
    committed_decoded = decode_served(committed, COMMIT_RESPONSE)
    assert committed_decoded["committed"] == [PATH]

    finalized = _finalize(client, capture_id, photo_packet(packet_id, [(PATH, BLOB)]), packet_id)
    assert finalized.status_code == 202, finalized.text
    finalize_decoded = decode_served(finalized.json(), FINALIZE_RESPONSE)
    assert finalize_decoded["status"] == "processing" and finalize_decoded["runId"]

    # The awaiting-files 202: the other FinalizeResponse shape.
    capture_id2 = _create(client, "packet-0002")["captureId"]
    _put(client, _register(client, capture_id2))
    _commit(client, capture_id2, [PATH])
    packet2 = photo_packet("packet-0002", [(PATH, BLOB), ("stills/two.jpg", b"twobytes")])
    finalized2 = _finalize(client, capture_id2, packet2, "packet-0002")
    assert finalized2.status_code == 202, finalized2.text
    awaiting = decode_served(finalized2.json(), FINALIZE_RESPONSE)
    assert awaiting["status"] == "awaiting_files" and awaiting["missing"] == ["stills/two.jpg"]


def test_events_poll_decode_including_empty_timeout(client: TestClient) -> None:
    capture_id = _create(client)["captureId"]
    _put(client, _register(client, capture_id))
    _commit(client, capture_id, [PATH])

    first = client.get(f"/v1/captures/{capture_id}/events", params={"after": 0})
    assert first.status_code == 200
    decoded = decode_served(first.json(), EVENTS_RESPONSE)
    assert decoded["events"], "the commit's files_committed event should be there"
    for event in decoded["events"]:
        assert isinstance(event["seq"], int)

    # Cursor poll: no events, cursor does not move.
    cursor = decoded["next"]
    again = client.get(f"/v1/captures/{capture_id}/events", params={"after": cursor})
    empty = decode_served(again.json(), EVENTS_RESPONSE)
    assert empty["events"] == [] and empty["next"] == cursor

    # Long-poll that times out empty: still a decodable EventsResponse.
    waited = client.get(f"/v1/captures/{capture_id}/events", params={"after": cursor, "wait": 1})
    assert waited.status_code == 200
    timed_out = decode_served(waited.json(), EVENTS_RESPONSE)
    assert timed_out["events"] == [] and timed_out["next"] == cursor


def test_event_data_swift_keys_carry_strings(client: TestClient) -> None:
    """The six Swift-decoded data keys must be strings-or-null on every served event.

    The event contract stores `data` as dict[str, Any]; Swift pins those six keys
    as String?. One non-string value there fails the whole EventsResponse decode,
    so every event on the walk is decoded, not just the first page.
    """

    capture_id = _create(client)["captureId"]
    _put(client, _register(client, capture_id))
    _commit(client, capture_id, [PATH])
    packet = photo_packet("packet-0001", [(PATH, BLOB)])
    finalized = _finalize(client, capture_id, packet, "packet-0001")
    assert finalized.status_code == 202, finalized.text

    # The typed payloads production emits (orchestrator/events.py, api/finalize.py),
    # built by the same contract constructors and dumped the same way.
    from server.api.events import emit
    from server.contracts.api import RetakeRequestData, StageData, VerdictReadyData

    deps = client.app_state
    run_id = finalized.json()["runId"]
    emit(
        deps,
        capture_id,
        "stage",
        StageData(stage="dense", status="running", attempt=2, runId=run_id, gpu="L4", durationS=1.5).model_dump(
            mode="json"
        ),
    )
    emit(
        deps, capture_id, "verdict_ready", VerdictReadyData(runId=run_id, kind="manual_review").model_dump(mode="json")
    )
    emit(
        deps,
        capture_id,
        "retake_request",
        RetakeRequestData(runId=run_id, viewsNeeded=["v1", "v2"], memberActions=["retake_meter_closeups"]).model_dump(
            mode="json"
        ),
    )
    emit(
        deps,
        capture_id,
        "failed",
        {"code": "stage_exhausted:dense:oom", "message": "the dense stage gave up", "next": "manual_review"},
    )

    body = client.get(f"/v1/captures/{capture_id}/events", params={"after": 0}).json()
    decoded = decode_served(body, EVENTS_RESPONSE)
    kinds = {event["type"] for event in decoded["events"]}
    assert {"files_committed", "stage", "verdict_ready", "retake_request", "failed"} <= kinds
    for event in decoded["events"]:
        if event["data"] is None:
            continue
        for key in ("code", "kind", "stage", "status", "runId", "next"):
            value = event["data"].get(key)
            assert value is None or isinstance(value, str), (
                f"event {event['type']}.{key} would fail Swift's String? decode: {value!r}"
            )


def test_confirmation_event_decodes(client: TestClient) -> None:
    capture_id = _create(client, "packet-confirm")["captureId"]
    registered = _register(
        client,
        capture_id,
        path="stills/s1.jpg",
        data=b"meter",
        meta={
            "still": {
                "id": "s1",
                "purpose": "meter_close",
                "img": "stills/s1.jpg",
                "w": 2,
                "h": 2,
                "timestamp": 0,
                "orientation": 1,
            }
        },
    )
    _put(client, registered, data=b"meter")
    _commit(client, capture_id, ["stills/s1.jpg"], data=b"meter")
    response = client.post(
        f"/v1/captures/{capture_id}/confirmations",
        json={
            "fieldId": "meter_number",
            "kind": "confirmed",
            "value": "ABC123",
            "imageId": "s1",
            "clientAt": "2026-09-26T00:00:00Z",
        },
    )
    assert response.status_code == 201, response.text
    listed = client.get(f"/v1/captures/{capture_id}/confirmations")
    assert listed.status_code == 200
    decode_served(listed.json(), struct_of({"confirmations": (array_of(struct_of({})), True)}))
    body = client.get(f"/v1/captures/{capture_id}/events", params={"after": 0}).json()
    decoded = decode_served(body, EVENTS_RESPONSE)
    assert "confirmation" in {event["type"] for event in decoded["events"]}


def test_sweeper_expiry_event_decodes(client: TestClient) -> None:
    from datetime import UTC, datetime

    from server.api.sweeper import ingest_sweep

    capture_id = _create(client, "packet-exp")["captureId"]
    deps = client.app_state
    record = deps.state.get_capture(capture_id)
    record.finalize_by = "2026-09-26T00:00:00Z"
    deps.state.put_capture(record)
    ingest_sweep(deps)
    body = client.get(f"/v1/captures/{capture_id}/events", params={"after": 0}).json()
    decoded = decode_served(body, EVENTS_RESPONSE)
    expired = [event for event in decoded["events"] if event["type"] == "failed"]
    assert expired and expired[0]["data"]["code"] == "capture_expired"
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    swept = deps.state.get_capture(capture_id)
    assert swept is not None and swept.status == "expired"
    assert swept.finalize_by < now  # the sweep, not the test, expired it


def test_result_before_a_run_decodes_for_every_working_status(client: TestClient) -> None:
    for status in ("uploading", "awaiting_files", "processing"):
        capture_id = _create(client, f"packet-{status}")["captureId"]
        _set_status(client, capture_id, status)
        response = client.get(f"/v1/captures/{capture_id}/result")
        assert response.status_code == 200
        decoded = decode_served(response.json(), RESULT_RESPONSE)
        assert decoded["status"] == status and decoded["outcome"] is None
        assert decoded["viewsNeeded"] == [] and decoded["memberActions"] == []


def _outcome(decision: str, status: str) -> dict:
    """A compose-shaped outcome for the committed fixture verdicts."""

    if decision == "pass":
        return {
            "kind": "eligible",
            "profile": "C",
            "message": "A spot fits on this wall.",
            "viewsNeeded": [],
            "reasons": [],
            "arkitEpoch": "e1",
            "recommendedPlacement": {
                "wallId": "w1",
                "startSM": 1.2,
                "footprintM": {"w": 0.7874, "h": 1.0033, "d": 0.5588},
                "boxSceneM": {"centerM": [1.59, 0.5, 0.3], "sizeM": [0.7874, 1.0033, 0.5588], "yawDeg": 0},
                "boxArkitWorld": {
                    "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                    "size": [0.7874, 1.0033, 0.5588],
                },
                "confidence": 0.8,
                "profile": "C",
            },
        }
    if decision == "needs_views":
        return {
            "kind": "needs_more_photos",
            "profile": "C",
            "message": "One more look at the meter.",
            "viewsNeeded": [
                {
                    "id": "vn1",
                    "kind": "band",
                    "criteria": ["clearance_gas_meter"],
                    "why": "the meter's clearance is not seen",
                    "promptId": "prompt.meter_band",
                    "prompt": {"title": "Show the meter band", "body": "Walk back so the meter is in frame."},
                    "band": "wall",
                    "wallId": "w1",
                },
                {
                    "id": "vn2",
                    "kind": "close_up",
                    "criteria": ["meter_visible"],
                    "why": "the meter number is unreadable",
                    "promptId": "prompt.meter_closeup",
                    "prompt": {"title": "Meter close-up", "body": "Stand close to the meter."},
                    "referenceCrop": {"image": "stills/s1.jpg", "boxPx": [0, 0, 100, 100]},
                },
            ],
            "reasons": [
                {
                    "criterion": "clearance_gas_meter",
                    "text": "The distance to the gas meter is not seen.",
                    "measuredFt": None,
                    "plusMinusFt": 0.5,
                    "thresholdFt": 5.0,
                    "comparison": "at_least",
                }
            ],
        }
    kind = "manual_review" if status != "needs_views" else "needs_more_photos"
    body = {
        "kind": kind,
        "profile": "C",
        "message": "Review required before a placement is shown.",
        "viewsNeeded": [],
        "reasons": [],
    }
    if decision == "reject":
        body["reasons"] = [
            {
                "criterion": "cable_run",
                "text": "The cable run is too long.",
                "measuredFt": 18.0,
                "thresholdFt": 13.6,
                "comparison": "at_most",
            }
        ]
    return body


@pytest.mark.parametrize(
    ("packet_id", "decision", "status"),
    [
        ("packet-complete", "pass", "complete"),
        ("packet-manual", "manual_review", "manual_review"),
        ("packet-reject", "reject", "manual_review"),
        ("packet-needs", "needs_views", "needs_views"),
    ],
)
def test_ready_result_decodes_for_each_reachable_terminal_status(
    client: TestClient, packet_id: str, decision: str, status: str
) -> None:
    assert status in TERMINAL_STATUSES
    capture_id = _create(client, packet_id)["captureId"]
    stored = {
        "runId": "run_stale_marker",
        "status": "manual_review",  # what the stub composer wrote; the route overlays record.status
        "viewsNeeded": [],
        "memberActions": [],
        "outcome": _outcome(decision, status),
    }
    if decision == "needs_views":
        stored["viewsNeeded"] = stored["outcome"]["viewsNeeded"]
        stored["criteria"] = [
            {"id": "clearance_gas_meter", "outcome": "unsure", "unsureCause": "unobserved", "coverage": "unobserved"},
            {
                "id": "meter_visible",
                "outcome": "pass",
                "measuredFt": 6.2,
                "plusMinusFt": 0.3,
                "thresholdFt": 5.0,
                "coverage": "observed",
            },
        ]
    _seed_ready_result(client, capture_id, f"run_{packet_id}", stored, status)

    response = client.get(f"/v1/captures/{capture_id}/result")
    assert response.status_code == 200, response.text
    decoded = decode_served(response.json(), RESULT_RESPONSE)
    assert decoded["status"] == status
    assert decoded["outcome"] is not None
    assert decoded["reviewUrl"] == f"/v1/captures/{capture_id}/review"
    # AR numbers the app checks later: the contract's Mat4 is 16 numbers.
    placement = decoded["outcome"]["recommendedPlacement"]
    if placement is not None and placement["boxArkitWorld"] is not None:
        assert len(placement["boxArkitWorld"]["pose"]) == 16
        assert len(placement["boxArkitWorld"]["size"]) == 3
    if status == "needs_views":
        # INTEGRATION.md: top-level viewsNeeded and outcome.viewsNeeded are the same objects.
        assert decoded["viewsNeeded"] == decoded["outcome"]["viewsNeeded"]


COMPOSED_FIXTURES = [
    # (fixture dir, scene dir, reads file, the status the coordinator sets on the capture)
    ("synthetic", "synthetic", "reads.auto_fill.json", "complete"),
    ("photo", "photo", None, "needs_views"),
    ("reject", "synthetic", None, "manual_review"),
]


@pytest.mark.parametrize(("case", "scene_dir", "reads_name", "status"), COMPOSED_FIXTURES)
def test_real_composer_output_decodes_for_each_committed_synthetic_case(
    client: TestClient, tmp_path: Path, case: str, scene_dir: str, reads_name: str | None, status: str
) -> None:
    """The real `compose_result` output — not a hand-written stand-in — decodes as the app reads it.

    The result stage writes `result.json` and the coordinator uploads it as the
    `result/result.json` artifact the route serves. These cases run the real composer
    over the committed synthetic fixtures and serve exactly those bytes, so a producer
    change that breaks iOS decoding fails here instead of escaping the check.
    """

    from server.contracts.jobs import CaptureRecord, RunRecord
    from server.result.compose import compose_result
    from server.result.config import ResultConfig
    from server.result.copy import load_catalogue
    from server.result.tests.support import DATA as COMPOSE_DATA

    raw = json.loads((COMPOSE_DATA / "capture.sample.json").read_text())
    epochs = list(raw.pop("epochs"))
    capture = CaptureRecord.model_validate(raw)
    run = RunRecord.model_validate(json.loads((COMPOSE_DATA / "run.ok.json").read_text()))
    composed = compose_result(
        capture,
        run,
        COMPOSE_DATA / scene_dir,
        COMPOSE_DATA / case / "verdicts",
        tmp_path,
        reads=COMPOSE_DATA / reads_name if reads_name else None,
        scale=None,
        catalogue=load_catalogue(COMPOSE_DATA / "prompts.en-US.json"),
        config=ResultConfig(catalogue_path=COMPOSE_DATA / "prompts.en-US.json"),
        epochs=epochs,
    )
    assert composed.response.status == status
    stored = json.loads((tmp_path / "result.json").read_text())

    capture_id = _create(client, f"packet-composed-{case}")["captureId"]
    _seed_ready_result(client, capture_id, f"run-composed-{case}", stored, status)

    response = client.get(f"/v1/captures/{capture_id}/result")
    assert response.status_code == 200, response.text
    decoded = decode_served(response.json(), RESULT_RESPONSE)
    assert decoded["status"] == status
    assert decoded["outcome"] is not None
    assert decoded["outcome"]["kind"] == composed.response.outcome.kind
    assert decoded["reviewUrl"] == f"/v1/captures/{capture_id}/review"
    # AR numbers the app checks later: the contract's Mat4 is 16 numbers.
    placement = decoded["outcome"]["recommendedPlacement"]
    if placement is not None and placement["boxArkitWorld"] is not None:
        assert len(placement["boxArkitWorld"]["pose"]) == 16
        assert len(placement["boxArkitWorld"]["size"]) == 3


@pytest.mark.parametrize("status", sorted(STATUSES_WITHOUT_OUTCOME))
def test_result_without_a_run_for_status_without_outcome_decodes(client: TestClient, status: str) -> None:
    """failed/expired with no outcome is the app's statusesWithoutOutcome final read."""

    capture_id = _create(client, f"packet-{status}-noout")["captureId"]
    _set_status(client, capture_id, status)
    response = client.get(f"/v1/captures/{capture_id}/result")
    assert response.status_code == 200
    decoded = decode_served(response.json(), RESULT_RESPONSE)
    assert decoded["status"] == status and decoded["outcome"] is None

    events = client.get(f"/v1/captures/{capture_id}/events", params={"after": 0}).json()
    events_decoded = decode_served(events, EVENTS_RESPONSE)
    assert events_decoded["status"] == status  # EventsResponse carries the terminal status


def test_result_read_of_a_deleted_capture_decodes(client: TestClient) -> None:
    capture_id = _create(client, "packet-del")["captureId"]
    deleted = client.delete(f"/v1/captures/{capture_id}")
    assert deleted.status_code == 204
    response = client.get(f"/v1/captures/{capture_id}/result")
    assert response.status_code == 410
    assert decode_error(response.json(), "result-deleted") == ["capture_deleted"]


def test_review_and_preview_refusals_decode(client: TestClient) -> None:
    capture_id = _create(client, "packet-review")["captureId"]
    review = client.get(f"/v1/captures/{capture_id}/review")
    assert review.status_code == 403  # HSS_AUTH_MODE=static: review is for reviewers
    assert decode_error(review.json(), "review-403") == ["forbidden"]
    preview = client.get(f"/v1/captures/{capture_id}/preview.ply")
    assert preview.status_code == 403
    decode_error(preview.json(), "preview-403")

    # A reviewer sees 404 while no review page exists.
    reviewer = TestClient(client.app)
    reviewer.headers["Authorization"] = "Bearer " + "f" * 40
    missing = reviewer.get(f"/v1/captures/{capture_id}/review")
    assert missing.status_code == 404
    assert decode_error(missing.json(), "review-404") == ["result_not_ready"]
    missing_preview = reviewer.get(f"/v1/captures/{capture_id}/preview.ply")
    assert missing_preview.status_code == 404
    decode_error(missing_preview.json(), "preview-404")


def test_every_error_body_the_walk_produces_decodes(client: TestClient) -> None:
    anon = _anon(client)
    probes: list[tuple[str, Any]] = []

    response = anon.post("/v1/captures", json=create_body("packet-err"), headers={"Idempotency-Key": "packet-err"})
    probes.append(("401-create", response))
    response = client.post("/v1/captures", json=create_body("packet-err"))
    probes.append(("422-no-key", response))
    client.post("/v1/captures", json=create_body("packet-err"), headers={"Idempotency-Key": "packet-err"})
    response = client.post(
        "/v1/captures",
        json=create_body("packet-err", tier="arkit_lidar"),
        headers={"Idempotency-Key": "packet-err"},
    )
    probes.append(("409-idempotency", response))
    response = client.post(
        "/v1/captures",
        json=create_body("packet-err2", formatVersion="0.3"),
        headers={"Idempotency-Key": "packet-err2"},
    )
    probes.append(("422-format", response))

    capture_id = _create(client, "packet-err3")["captureId"]
    probes.append(("404-capture", client.get("/v1/captures/cap_nonexistent/result")))
    probes.append(("401-events", anon.get(f"/v1/captures/{capture_id}/events")))
    probes.append(("422-after", client.get(f"/v1/captures/{capture_id}/events", params={"after": -1})))
    probes.append(("422-empty-files", client.post(f"/v1/captures/{capture_id}/files", json={"files": []})))
    response = client.post(
        f"/v1/captures/{capture_id}/files",
        json={
            "files": [
                {
                    "path": "stills/x.jpg",
                    "bytes": 4,
                    "sha256": sha256_hex(BLOB),
                    "md5": "not-base64",
                    "role": "still",
                    "contentType": "image/jpeg",
                }
            ]
        },
    )
    probes.append(("422-bad-md5", response))
    not_found = client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": PATH, "sha256": sha256_hex(BLOB)}]},
    )
    probes.append(("200-commit-notfound", not_found))  # decodes as CommitResponse, not an error

    _put(client, _register(client, capture_id))
    conflict = client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": PATH, "sha256": sha256_hex(b"different")}]},
    )
    probes.append(("409-sha256", conflict))

    raw = json.dumps(photo_packet("packet-err3", [(PATH, BLOB)])).encode()
    tampered = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={
            "Idempotency-Key": "packet-err3",
            "Content-Type": "application/json",
            "X-Packet-Sha256": sha256_hex(b"nope"),
        },
    )
    probes.append(("422-packet-sha", tampered))
    probes.append(
        ("409-packet-id", _finalize(client, capture_id, photo_packet("packet-other", [(PATH, BLOB)]), "packet-other"))
    )

    for label, probe in probes:
        if label == "200-commit-notfound":
            assert probe.status_code == 200
            decode_served(probe.json(), COMMIT_RESPONSE)
            continue
        assert probe.status_code >= 400, f"{label}: {probe.status_code}"
        decode_error(probe.json(), label)


def test_too_many_polls_error_decodes(client: TestClient) -> None:
    capture_id = _create(client, "packet-polls")["captureId"]
    client.app_state.polls[capture_id] = 8
    response = client.get(f"/v1/captures/{capture_id}/events", params={"after": 0})
    assert response.status_code == 429
    assert decode_error(response.json(), "429-polls") == ["too_many_polls"]


def test_payload_too_large_error_decodes(client: TestClient) -> None:
    capture_id = _create(client, "packet-big")["captureId"]
    response = client.post(
        f"/v1/captures/{capture_id}/files",
        content=b"x" * 3_000_001,
        headers={"Content-Type": "application/json", "Content-Length": "3000001"},
    )
    assert response.status_code == 413
    assert decode_error(response.json(), "413-payload") == ["payload_too_large"]


# --- the documented error shape on answers the walk does not expect -----------


def test_route_miss_404_uses_the_documented_api_error_shape(client: TestClient) -> None:
    """INTEGRATION.md: "Errors are ApiError". A route miss must decode as ErrorBody."""

    response = client.get("/v1/captures/cap_nonexistent/nope")
    assert response.status_code == 404
    assert decode_error(response.json(), "route-miss-404") == ["not_found"]


def test_method_not_allowed_uses_the_documented_api_error_shape(client: TestClient) -> None:
    response = client.patch("/v1/healthz")
    assert response.status_code == 405
    decode_error(response.json(), "405")
    # Starlette's required Allow list survives the ApiError rewrite.
    assert "GET" in response.headers["Allow"].split(", ")


def test_unhandled_exception_uses_the_documented_api_error_shape() -> None:
    """A 500 body the app's errorCodes can read, instead of plain text."""

    from server.api.app import create_app
    from server.common.config import Settings

    def boom() -> None:
        raise RuntimeError("stage exploded")

    fresh = TestClient(
        create_app(
            settings=Settings(
                STORAGE_BACKEND="volume",
                STATE_BACKEND="memory",
                VOLUME_ROOT="/tmp/decoder-contract-vol",
                CAPTURE_API_PUBLIC_URL="http://testserver",
                CAPTURE_API_TOKENS=f"device:{'d' * 40}",
                UPLOAD_URL_HMAC_SECRET="h" * 32,
                HSS_INGEST_HOOKS="null",
                HSS_AUTH_MODE="static",
                GIT_SHA="0123456789abcdef",
                VISION_EXTERNAL_CALLS="on",
                SMP_SERVICE_NAME="capture-api",
            )
        ),
        raise_server_exceptions=False,
    )
    fresh.app.add_api_route("/v1/_boom", boom, methods=["GET"])
    fresh.headers["Authorization"] = "Bearer " + "d" * 40
    response = fresh.get("/v1/_boom")
    assert response.status_code == 500
    assert decode_error(response.json(), "500") == ["internal"]
