"""The finalize → orchestrator handoff, driven through the API.

``HSS_INGEST_HOOKS=auto`` is the documented hook mode (SD-73: the dev deploy
runs it with ``HSS_ORCH_STAGE_STUBS=1``). ``resolve_hooks`` then returns
``OrchestratorHooks``, and the background finalize thread hands the promoted
packet to ``server.orchestrator.start_run`` with the API's state and storage
installed as orchestrator services (``hooks.py`` ``_bind`` →
``services.bind``). With ``HSS_INGEST_HOOKS=null`` none of that runs: finalize
returns ``processing`` and no run ever exists.

These tests drive the real FastAPI app over HTTP with the committed mini-arkit
packet fixture, the orchestrator's stage stubs, memory/local backends, and no
Modal, GPU, network or credentials. The orchestrator's supported test knob
``HSS_ORCH_TEST_FAIL_STAGE`` (the same knob the orchestrator fixture proof
uses) supplies the controlled stage failure. Events and results are read back
through the routes a device actually polls.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from server.api.tests.conftest import ApiClient
from server.common.lease import LeaseBusy

FIXTURE = Path(__file__).resolve().parents[3] / "data" / "fixtures" / "packets" / "mini-arkit"
PACKET_RAW = (FIXTURE / "packet.json").read_bytes()
PACKET = json.loads(PACKET_RAW)
PACKET_ID = PACKET["packetId"]


def _content_type(role: str) -> str:
    if role in {"still", "keyframe"}:
        return "image/jpeg"
    if role == "stream":
        return "application/gzip"
    return "application/octet-stream"


def _upload_and_commit(client: ApiClient, capture_id: str) -> None:
    """Register, PUT and commit every file the packet lists, from the fixture bytes."""
    for entry in PACKET["files"]:
        data = (FIXTURE / entry["path"]).read_bytes()
        body = {
            "path": entry["path"],
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "md5": base64.b64encode(hashlib.md5(data).digest()).decode(),
            "role": entry["role"],
            "contentType": _content_type(entry["role"]),
        }
        registered = client.post(f"/v1/captures/{capture_id}/files", json={"files": [body]})
        assert registered.status_code == 200, registered.text
        item = registered.json()["files"][0]
        if item["state"] == "pending":
            target = item["upload"]
            put = client.put(urlparse(target["url"]).path, content=data, headers=target["headers"])
            assert put.status_code == 200, put.text
        committed = client.post(
            f"/v1/captures/{capture_id}/files:commit",
            json={"files": [{"path": entry["path"], "sha256": body["sha256"]}]},
        )
        assert committed.status_code == 200, committed.text


def _finalize(client: ApiClient, capture_id: str) -> dict:
    response = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=PACKET_RAW,
        headers={
            "Idempotency-Key": PACKET_ID,
            "Content-Type": "application/json",
            "X-Packet-Sha256": hashlib.sha256(PACKET_RAW).hexdigest(),
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def _events(client: ApiClient, capture_id: str) -> list[dict]:
    """Page the event log to the end through the device route."""
    events: list[dict] = []
    after = 0
    while True:
        body = client.get(f"/v1/captures/{capture_id}/events", params={"after": after, "wait": 0})
        assert body.status_code == 200, body.text
        page = body.json()
        events.extend(page["events"])
        if not page["events"] or page["next"] == after:
            return events
        after = page["next"]


def _terminals(events: list[dict]) -> list[str]:
    return [event["type"] for event in events if event["type"] in {"verdict_ready", "retake_request", "failed"}]


def test_finalize_with_auto_hooks_runs_the_orchestrator_to_the_verdict(auto_client: ApiClient) -> None:
    client = auto_client
    created = client.post(
        "/v1/captures",
        json={
            "packetId": PACKET_ID,
            "formatVersion": "0.4",
            "tier": PACKET["session"]["tier"],
            "device": {"model": "iPhone", "systemVersion": "18", "appVersion": "1"},
        },
        headers={"Idempotency-Key": PACKET_ID},
    )
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]

    _upload_and_commit(client, capture_id)
    body = _finalize(client, capture_id)

    # The 202 names the accept states only; the run id is deterministic from the bytes.
    assert body["status"] == "processing"
    assert body["missing"] == []
    expected_run_id = "run_" + hashlib.sha256(capture_id.encode() + b"\0" + PACKET_RAW).hexdigest()[:16]
    assert body["runId"] == expected_run_id

    deps = client.app_state
    run = deps.state.get_run(body["runId"])
    assert run is not None, "finalize did not hand off to the orchestrator: no run record"
    assert run.capture_id == capture_id

    # The stub verdict is manual_review with no views needed (outcome table row 14):
    # the capture status must land on the run's outcome, not stay on processing.
    assert run.status == "done"
    assert run.outcome_kind == "manual_review"
    assert run.failure_code is None
    record = deps.state.get_capture(capture_id)
    assert record.status == "manual_review"
    assert record.run_id == run.run_id
    assert deps.state.get_stage(run.run_id, "result").status == "done"

    # Poll the device event feed: every stage ran, and exactly one terminal verdict.
    events = _events(client, capture_id)
    staged = {event["data"].get("stage") for event in events if event["type"] == "stage"}
    assert {"validate", "poses", "scale", "criteria", "result"} <= staged
    assert _terminals(events) == ["verdict_ready"]

    # The result route serves the run's composed verdict with the capture status.
    result = client.get(f"/v1/captures/{capture_id}/result")
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["status"] == "manual_review"
    assert payload["outcome"]["kind"] == "manual_review"
    assert payload["runId"] == run.run_id


def test_orchestrator_stage_failure_is_recorded_not_a_500(
    auto_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The orchestrator's supported failure knob: `scale` exhausts its attempts.
    monkeypatch.setenv("HSS_ORCH_TEST_FAIL_STAGE", "scale:compute_error:2")
    client = auto_client
    created = client.post(
        "/v1/captures",
        json={
            "packetId": PACKET_ID,
            "formatVersion": "0.4",
            "tier": PACKET["session"]["tier"],
            "device": {"model": "iPhone", "systemVersion": "18", "appVersion": "1"},
        },
        headers={"Idempotency-Key": PACKET_ID},
    )
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]

    _upload_and_commit(client, capture_id)
    body = _finalize(client, capture_id)  # a 202, not a 500: the failure lands off the request path
    assert body["status"] == "processing"

    deps = client.app_state
    run = deps.state.get_run(body["runId"])
    assert run is not None, "finalize did not hand off to the orchestrator: no run record"

    # Row 3 of the outcome table: a required stage exhausts its attempts, the run
    # records the failure, and the capture lands in manual_review for a person.
    assert run.status == "failed"
    assert run.failure_code == "stage_exhausted:scale:compute_error"
    record = deps.state.get_capture(capture_id)
    assert record.status == "manual_review"

    events = _events(client, capture_id)
    failed = [event for event in events if event["type"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["data"]["code"] == "stage_exhausted:scale:compute_error"
    assert failed[0]["data"]["next"] == "manual_review"
    assert _terminals(events) == ["failed"]

    # Even the failed run publishes the manual result a device can read.
    result = client.get(f"/v1/captures/{capture_id}/result")
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["status"] == "manual_review"
    assert payload["outcome"]["kind"] == "manual_review"


def test_confirmation_busy_is_retryable_and_replays_hook(auto_client: ApiClient) -> None:
    client = auto_client
    created = client.post(
        "/v1/captures",
        json={
            "packetId": PACKET_ID,
            "formatVersion": "0.4",
            "tier": PACKET["session"]["tier"],
            "device": {"model": "iPhone", "systemVersion": "18", "appVersion": "1"},
        },
        headers={"Idempotency-Key": PACKET_ID},
    )
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]
    _upload_and_commit(client, capture_id)
    _finalize(client, capture_id)
    deps = client.app_state
    original = deps.hooks.on_confirmation
    calls = {"count": 0}

    def busy_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise LeaseBusy("correction lease is busy")
        return original(*args, **kwargs)

    deps.hooks.on_confirmation = busy_once
    answer = {
        "fieldId": "meter_number",
        "kind": "corrected",
        "value": "12345",
        "imageId": PACKET["keyframes"][0]["id"],
        "clientAt": "2026-10-16T00:00:00.000Z",
    }
    busy = client.post(f"/v1/captures/{capture_id}/confirmations", json=answer)
    assert busy.status_code == 503, busy.text
    slot = deps.state.get_kv(f"confirm:{capture_id}:meter_number")
    assert slot["hook_applied"] is False
    replay = client.post(f"/v1/captures/{capture_id}/confirmations", json=answer)
    assert replay.status_code == 200, replay.text
    assert deps.state.get_kv(f"confirm:{capture_id}:meter_number")["hook_applied"] is True
    assert calls["count"] == 2


@pytest.mark.parametrize("boundary", ["before_journal", "after_journal", "before_ack"])
def test_confirmation_storage_fault_never_acknowledges_or_duplicates(
    auto_client: ApiClient, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    from server.orchestrator.corrections import correction_snapshot, latest_publication

    client = auto_client
    created = client.post(
        "/v1/captures",
        json={
            "packetId": PACKET_ID,
            "formatVersion": "0.4",
            "tier": PACKET["session"]["tier"],
            "device": {"model": "iPhone", "systemVersion": "18", "appVersion": "1"},
        },
        headers={"Idempotency-Key": PACKET_ID},
    )
    assert created.status_code == 201
    capture_id = created.json()["captureId"]
    _upload_and_commit(client, capture_id)
    run_id = _finalize(client, capture_id)["runId"]
    state = client.app_state.state
    insert = state.put_if_absent
    store = state.put_kv
    hit = []

    def journal(key, row):
        if key.startswith("capture_correction:") and boundary != "before_ack":
            hit.append(key)
            if boundary == "after_journal":
                assert insert(key, row)
            raise LeaseBusy("injected storage interruption")
        return insert(key, row)

    def acknowledgment(key, row):
        if key.startswith("confirm:") and row.get("hook_applied") and boundary == "before_ack":
            hit.append(key)
            raise LeaseBusy("injected acknowledgment interruption")
        return store(key, row)

    answer = {
        "fieldId": "meter_number",
        "kind": "corrected",
        "value": "12345",
        "imageId": PACKET["keyframes"][0]["id"],
        "clientAt": "2026-10-16T00:00:00.000Z",
    }
    with monkeypatch.context() as patch:
        patch.setattr(state, "put_if_absent", journal)
        patch.setattr(state, "put_kv", acknowledgment)
        interrupted = client.post(f"/v1/captures/{capture_id}/confirmations", json=answer)
    assert hit, "the fault boundary was never reached"
    assert interrupted.status_code == 503, interrupted.text
    retry = client.post(f"/v1/captures/{capture_id}/confirmations", json=answer)
    assert retry.status_code in {200, 201}, retry.text
    run = state.get_run(run_id)
    rows = correction_snapshot(state, run)
    assert [row["confirmationId"] for row in rows] == [retry.json()["confirmationId"]]
    assert latest_publication(state, run_id)["correctionIds"] == [retry.json()["confirmationId"]]
    assert client.get(f"/v1/captures/{capture_id}/result").json()["meter"]["value"] == "12345"


def test_confirmation_replay_recomposes_once(auto_client: ApiClient) -> None:
    """A corrected answer POSTed twice recomposes the result exactly once.

    The route's fingerprint check short-circuits the replayed confirmation
    before the hook runs, so the second POST returns the stored row without
    touching the run: the result stage runs the original pass plus one
    recomposition, never three times.
    """
    client = auto_client
    created = client.post(
        "/v1/captures",
        json={
            "packetId": PACKET_ID,
            "formatVersion": "0.4",
            "tier": PACKET["session"]["tier"],
            "device": {"model": "iPhone", "systemVersion": "18", "appVersion": "1"},
        },
        headers={"Idempotency-Key": PACKET_ID},
    )
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]

    _upload_and_commit(client, capture_id)
    body = _finalize(client, capture_id)
    run_id = body["runId"]

    deps = client.app_state
    assert deps.state.get_stage(run_id, "result").status == "done"

    answer = {
        "fieldId": "meter_number",
        "kind": "corrected",
        "value": "12345",
        "imageId": PACKET["stills"][0]["id"],
        "clientAt": "2026-10-16T00:00:00.000Z",
    }
    first = client.post(f"/v1/captures/{capture_id}/confirmations", json=answer)
    assert first.status_code == 201, first.text
    replay = client.post(f"/v1/captures/{capture_id}/confirmations", json=answer)
    assert replay.status_code == 200, replay.text

    run = deps.state.get_run(run_id)
    assert run.status == "done"
    # The recomposed pass records attempt 1 again (the coordinator recomputes
    # spawn attempts), so "recomposed exactly once" is read from the event log:
    # the original pass plus one re-run of each stage — the fingerprint
    # short-circuit keeps the replayed POST from triggering a third pass.
    events = deps.state.events_after(capture_id, 0, limit=500)
    for stage in ("criteria", "result"):
        started = [
            event
            for event in events
            if event.type == "stage" and event.data.get("stage") == stage and event.data.get("status") == "running"
        ]
        assert len(started) == 2, [(e.type, e.data) for e in started]
    result = client.get(f"/v1/captures/{capture_id}/result")
    assert result.status_code == 200, result.text
    assert result.json()["meter"]["value"] == "12345"
