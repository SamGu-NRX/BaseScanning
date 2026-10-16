"""The joined runtime, end to end: dispatch faults, event visibility, amendments.

Three sibling fixes landed on their own branches and are imported here as scoped
diffs, so their regressions pass on the joined tree but nothing proves the fixes
*interact* over the real API:

- #251 (`13f9f4a6`, cut from `2d0911f8`): amendment runs hydrate their own packet,
  device confirmations apply, retries get a fresh timeout budget, and the final
  status reads the run's own rules profile.
- #252 (`64dfb7a9`, cut from `2d0911f8`): a failed ``on_finalized`` dispatch stays
  retryable, background exceptions surface as retryable failures, and concurrent
  file-index merges run under the state lease.
- #266 (`ca7448e2`, cut from `57f57ff8`): ``MemoryState.append_event`` assigns the
  per-capture ``seq`` under a dedicated lock, so the ``events_after`` cursor the
  client polls never skips or repeats an event.

This file is that proof: one API-level journey per interaction, over the
documented dev settings (SD-73) — memory state, local volume storage, static test
auth, an HMAC upload secret, stage stubs with prewarm and external vision calls
off. No Modal, no GPU, no network, no credentials.

The journeys reuse the committed ``mini-arkit`` packet fixture and the capture
front door exactly as ``test_capture_placement_frontdoor.py`` walks it, so the
existing capture journey — through the real, ``stubs_exempt`` placement stage —
still completes here; the three tests below pin what the joins add on top of it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.api.app import create_app
from server.api.deps import drain_finalize, extras
from server.common.config import Settings
from server.contracts.jobs import run_id_for_capture

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKET_FIXTURE = REPO_ROOT / "backend" / "data" / "fixtures" / "packets" / "mini-arkit"

TERMINAL_OUTCOMES = {"verdict_ready", "retake_request"}

# The documented dev setting, as in the front-door test: the real hook chain with
# stubbed stages (placement is stubs_exempt and runs the real solver).
TEMPLATE_ENV = {
    "HSS_ORCH_STAGE_STUBS": "1",
    "HSS_ORCH_PREWARM": "0",
    "VISION_EXTERNAL_CALLS": "off",
    "STATE_BACKEND": "memory",
    "STORAGE_BACKEND": "volume",
    "SMP_ENVIRONMENT": "dev",
}


@pytest.fixture
def journey(tmp_path, monkeypatch):
    """The app behind the capture front door, plus a private copy of the fixture."""
    for key, value in TEMPLATE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("VOLUME_ROOT", str(tmp_path / "vol"))
    root = tmp_path / "packet"
    shutil.copytree(PACKET_FIXTURE, root)
    packet = json.loads((root / "packet.json").read_text())
    settings = Settings(
        SMP_ENVIRONMENT="dev",
        HSS_AUTH_MODE="static",
        # Synthetic, explicitly static test auth — no real principal.
        CAPTURE_API_TOKENS=f"device:{'d' * 40}",
        UPLOAD_URL_HMAC_SECRET="h" * 32,
        STORAGE_BACKEND="volume",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "vol"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        HSS_INGEST_HOOKS="auto",
        VISION_EXTERNAL_CALLS="off",
        GIT_SHA="0123456789abcdef",
        SMP_SERVICE_NAME="capture-api",
    )
    app = create_app(settings=settings)
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {'d' * 40}"
    return root, packet, client, app


def _content_type(role: str) -> str:
    if role == "stream":
        return "application/gzip"
    if role in {"features", "depth", "confidence", "mesh"}:
        return "application/octet-stream"
    return "image/jpeg"


def _poll_events(client: TestClient, capture_id: str) -> list[list[dict]]:
    """Walk the events cursor to quiescence, keeping every page as delivered."""
    pages: list[list[dict]] = []
    after = 0
    for _ in range(60):
        page = client.get(f"/v1/captures/{capture_id}/events", params={"after": after}).json()
        assert page["next"] >= after, "the events cursor moved backwards"
        pages.append(page["events"])
        if page["next"] == after:
            return pages
        after = page["next"]
    pytest.fail("event feed never stopped moving")


def _flat(client: TestClient, capture_id: str) -> list[dict]:
    return [event for page in _poll_events(client, capture_id) for event in page]


def _register_and_commit_fixture(root: Path, packet: dict, client: TestClient, app, capture_id: str) -> None:
    """Register, upload and commit every fixture file the manifest lists."""
    registered_paths = []
    for entry in packet["files"]:
        data = (root / entry["path"]).read_bytes()
        item = {
            "path": entry["path"],
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "md5": base64.b64encode(hashlib.md5(data).digest()).decode(),
            "role": entry.get("role", "still"),
            "contentType": _content_type(entry.get("role", "still")),
        }
        registered = client.post(f"/v1/captures/{capture_id}/files", json={"files": [item]})
        assert registered.status_code == 200, registered.text
        upload = registered.json()["files"][0]["upload"]
        # The signed URL is its own capability: no bearer header on the PUT.
        put = TestClient(app).put(upload["url"], content=data, headers=upload.get("headers") or {})
        assert put.status_code == 200, put.text
        registered_paths.append({"path": item["path"], "sha256": item["sha256"]})
    commit = client.post(f"/v1/captures/{capture_id}/files:commit", json={"files": registered_paths})
    assert commit.status_code == 200, commit.text


def _finalize(
    client: TestClient,
    capture_id: str,
    raw: bytes,
    packet_id: str,
    *,
    amendment_id: str | None = None,
) -> dict:
    suffix = f"/amendments/{amendment_id}/finalize" if amendment_id else "/finalize"
    response = client.post(
        f"/v1/captures/{capture_id}{suffix}",
        content=raw,
        headers={
            "Idempotency-Key": amendment_id or packet_id,
            "Content-Type": "application/json",
            "X-Packet-Sha256": hashlib.sha256(raw).hexdigest(),
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def _open_front_door(root: Path, packet: dict, client: TestClient, app, packet_id: str) -> tuple[str, bytes]:
    """A capture with every packet file committed; returns (capture_id, packet bytes)."""
    document = dict(packet)
    document["packetId"] = packet_id
    raw = (json.dumps(document, indent=2) + "\n").encode()
    (root / "packet.json").write_bytes(raw)

    session = packet["session"]
    created = client.post(
        "/v1/captures",
        json={
            "packetId": packet_id,
            "formatVersion": packet["formatVersion"],
            "tier": session["tier"],
            "flow": "guided",
            "device": {
                "model": session["deviceModel"],
                "systemVersion": session["systemVersion"],
                "appVersion": session["appVersion"],
            },
        },
        headers={"Idempotency-Key": packet_id},
    )
    assert created.status_code == 201, created.text
    capture_id = created.json()["captureId"]
    _register_and_commit_fixture(root, packet, client, app, capture_id)
    return capture_id, raw


def test_failed_dispatch_stays_retryable_and_the_retry_lands_one_run_one_final(journey) -> None:
    """A broken ``on_finalized`` dispatch returns the retryable error and fabricates
    nothing; once the hook heals, one finalize retry establishes one run with one
    composed final event — not a second run, not a duplicate final."""
    root, packet, client, app = journey
    deps = app.state.deps

    # The sibling journey stays healthy while the fault capture is under test.
    sibling, _ = _open_front_door(root, packet, client, app, "join-dispatch-ok")
    accepted = _finalize(
        client,
        sibling,
        (json.dumps({**packet, "packetId": "join-dispatch-ok"}, indent=2) + "\n").encode(),
        "join-dispatch-ok",
    )
    drain_finalize(deps)
    assert deps.state.get_run(accepted["runId"]).status == "done"

    def boom(*args, **kwargs):
        raise RuntimeError("orchestrator start_run failed")

    original = deps.hooks.on_finalized
    deps.hooks.on_finalized = boom
    try:
        fault_id, raw = _open_front_door(root, packet, client, app, "join-dispatch-fault")
        response = _finalize(client, fault_id, raw, "join-dispatch-fault")
        assert response["status"] == "processing"
        drain_finalize(deps)
    finally:
        deps.hooks.on_finalized = original

    # The dispatch failed retryably — the existing 503 path, not a dead end.
    extra = extras(deps, fault_id)
    assert not extra.get("run_started")
    assert extra.get("started_run_id") is None
    assert deps.state.runs_for_capture(fault_id) == []
    assert deps.state.get_capture(fault_id).status == "uploading"
    events = _flat(client, fault_id)
    failures = [e for e in events if e["type"] == "failed" and e["data"].get("code") == "run_start_failed"]
    assert failures and failures[0]["data"]["next"] == "retry_finalize"
    # A controlled fault interrupts dispatch; it cannot fabricate a success artifact.
    assert not [e for e in events if e["type"] in TERMINAL_OUTCOMES]
    assert deps.state.get_capture(sibling).status == "manual_review"

    # The retry — same bytes, same deterministic run id — starts exactly one run.
    second = _finalize(client, fault_id, raw, "join-dispatch-fault")
    assert second["status"] == "processing"
    for _ in range(600):
        if extras(deps, fault_id).get("run_started"):
            break
        time.sleep(0.01)
    assert extras(deps, fault_id).get("run_started") is True
    drain_finalize(deps)

    runs = deps.state.runs_for_capture(fault_id)
    assert len(runs) == 1 and runs[0].status == "done"
    assert runs[0].run_id == run_id_for_capture(fault_id, raw)
    events = _flat(client, fault_id)
    finals = [e for e in events if e["type"] in TERMINAL_OUTCOMES]
    assert [e["type"] for e in finals] == ["verdict_ready"]
    # The wire Event carries no run_id field — the run id lives in the payload.
    assert finals[0]["data"]["runId"] == runs[0].run_id
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(set(seqs)) and seqs == list(range(1, len(seqs) + 1))
    # The retried journey still completes through the placement stage.
    assert any(
        e["type"] == "stage" and e["data"].get("stage") == "placement" and e["data"].get("status") == "done"
        for e in events
    )
    assert len(failures) == 1


def test_concurrent_event_producers_fully_visible_through_cursor_polling(journey) -> None:
    """Two producers appending to one capture — the API request path and the
    background finalize worker — never lose an event to the cursor: a poller
    walking ``after`` pages sees every seq exactly once, contiguously."""
    root, packet, client, app = journey
    deps = app.state.deps
    capture_id, _ = _open_front_door(root, packet, client, app, "join-events")
    _finalize(
        client, capture_id, (json.dumps({**packet, "packetId": "join-events"}, indent=2) + "\n").encode(), "join-events"
    )
    drain_finalize(deps)
    base_events = _flat(client, capture_id)
    base_count = len(base_events)
    assert base_count > 0

    producers_seen: list[list[int]] = [[], []]
    start = threading.Barrier(3)

    def produce(slot: int) -> None:
        start.wait()
        for index in range(30):
            rec = deps.state.append_event(capture_id, "capture_check", {"join": f"p{slot}-{index}"})
            producers_seen[slot].append(rec.seq)

    poller_pages: list[list[dict]] = []
    poller_after: list[int] = []
    final_cursor = base_count + 60

    def poll() -> None:
        # Poll until the cursor reaches the producers' last event, not until the
        # producers stop: a poller that stops with them can exit before draining
        # the tail, and the assertion below would blame the cursor for it.
        start.wait()
        after = 0
        give_up = time.monotonic() + 10.0
        while after < final_cursor and time.monotonic() < give_up:
            page = client.get(f"/v1/captures/{capture_id}/events", params={"after": after}).json()
            poller_pages.append(page["events"])
            poller_after.append(after)
            if page["next"] == after:
                time.sleep(0.02)
            else:
                after = page["next"]

    threads = [
        threading.Thread(target=produce, args=(0,)),
        threading.Thread(target=produce, args=(1,)),
        threading.Thread(target=poll),
    ]
    for thread in threads:
        thread.start()
    for thread in threads[:2]:
        thread.join()
    threads[2].join(timeout=15)
    assert not threads[2].is_alive()

    # Each producer drew a distinct slice of the sequence — never the same seq.
    assert producers_seen[0] and producers_seen[1]
    assert set(producers_seen[0]).isdisjoint(producers_seen[1])

    seqs = [event["seq"] for page in poller_pages for event in page]
    assert seqs == sorted(seqs), "a page arrived behind the cursor"
    assert len(seqs) == len(set(seqs)), "the cursor delivered an event twice"
    expected = list(range(1, final_cursor + 1))
    assert seqs == expected, "the cursor skipped an event a producer appended"

    # The store itself holds the complete, contiguous sequence.
    assert [e["seq"] for e in _flat(client, capture_id)] == expected


def test_amendment_hydrates_its_own_packet_bytes_and_files(journey) -> None:
    """An amendment run reads the amendment's packet manifest and files, not the
    original capture's — its run record carries its own packet_uri, and its new
    file resolves under the amendment prefix."""
    root, packet, client, app = journey
    deps = app.state.deps
    capture_id, raw = _open_front_door(root, packet, client, app, "join-amend")
    accepted = _finalize(client, capture_id, raw, "join-amend")
    parent_run = accepted["runId"]
    drain_finalize(deps)
    assert deps.state.get_run(parent_run).status == "done"

    # The stub result never asks for views, so take the capture to the state the
    # device would see (needs_views with one requested view) the way the
    # amendment suite seeds it — the parent run itself stays the real journey's.
    run = deps.state.get_run(parent_run)
    result_key = run.artifacts["result/result.json"]
    served = json.loads(deps.storage.get_bytes(result_key))
    served["viewsNeeded"] = [{"id": "wall-left"}]
    served["outcome"]["viewsNeeded"] = [{"id": "wall-left"}]
    deps.storage.put_bytes(result_key, json.dumps(served).encode(), "application/json")
    deps.state.set_status(capture_id, "needs_views")

    opened = client.post(
        f"/v1/captures/{capture_id}/amendments",
        json={"amendmentId": "amd-join-1", "answers": ["wall-left"]},
        headers={"Idempotency-Key": "amd-join-1"},
    )
    assert opened.status_code == 201, opened.text
    assert opened.json()["pathPrefix"] == "amendments/amd-join-1/"

    # The amendment's own still — a path and bytes the original capture never had.
    blob = b"amendment-wall-bytes-not-in-the-original"
    amend_path = "amendments/amd-join-1/stills/amend-wall.jpg"
    registered = client.post(
        f"/v1/captures/{capture_id}/files",
        json={
            "files": [
                {
                    "path": amend_path,
                    "bytes": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "md5": base64.b64encode(hashlib.md5(blob).digest()).decode(),
                    "role": "still",
                    "contentType": "image/jpeg",
                }
            ]
        },
    )
    assert registered.status_code == 200, registered.text
    upload = registered.json()["files"][0]["upload"]
    put = TestClient(app).put(upload["url"], content=blob, headers=upload.get("headers") or {})
    assert put.status_code == 200, put.text
    committed = client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": amend_path, "sha256": hashlib.sha256(blob).hexdigest()}]},
    )
    assert committed.status_code == 200, committed.text

    # The device's amendment packet: its own packetId, the parent's packet id,
    # and a manifest of exactly the files it re-captured.
    amend_packet = {
        "format": "house-capture-packet",
        "formatVersion": "0.4",
        "packetId": "join-amend-followup",
        "parentPacketId": "join-amend",
        "createdAt": "2026-10-15T00:00:00.000Z",
        "units": {"length": "meters", "angle": "degrees", "time": "seconds", "image": "pixels"},
        "conventions": {
            "world": "ARKit gravity, y up",
            "camera": "ARKit camera, looks along -z",
            "pose": "arkit-column-major",
            "intrinsics": "[fx, fy, cx, cy]",
            "pixel": "[u, v] top-left, v down",
        },
        "keyframes": [],
        "source": {"kind": "synthetic"},
        "session": {
            "id": "sess-amend",
            "startedAt": "2026-10-15T00:00:00.000Z",
            "startedAtUptime": 0.0,
            "appVersion": "1",
            "deviceModel": "iPhone",
            "systemVersion": "18",
            "tier": "photo_only",
            "lidarAvailable": False,
            "sceneDepthEnabled": False,
        },
        "stills": [
            {
                "id": "s1",
                "purpose": "house_overview",
                "img": "stills/amend-wall.jpg",
                "w": 2,
                "h": 2,
                "timestamp": 0,
                "orientation": 1,
            }
        ],
        "files": [
            {
                "path": "stills/amend-wall.jpg",
                "bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "role": "still",
            }
        ],
    }
    amend_raw = json.dumps(amend_packet).encode()
    finalize = _finalize(client, capture_id, amend_raw, "join-amend-followup", amendment_id="amd-join-1")
    assert finalize["status"] == "processing"
    drain_finalize(deps)

    runs = deps.state.runs_for_capture(capture_id)
    assert len(runs) == 2
    amend_run = max(runs, key=lambda item: item.created_at)
    assert amend_run.run_id == run_id_for_capture(capture_id, amend_raw)
    # The run's identity is the amendment's packet, not the original capture's.
    assert amend_run.packet_uri == f"captures/{capture_id}/amendments/amd-join-1/packet.json"
    assert amend_run.parent_run_id == parent_run
    assert amend_run.status == "done"
    assert amend_run.packet_sha256 == hashlib.sha256(amend_raw).hexdigest()

    events = _flat(client, capture_id)
    assert [e["type"] for e in events if e["type"] in TERMINAL_OUTCOMES] == ["verdict_ready", "verdict_ready"]
    # The wire Event carries no run_id field — the run id lives in the payload.
    assert any(e["data"].get("runId") == amend_run.run_id for e in events if e["type"] == "verdict_ready")
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(set(seqs)) and seqs == list(range(1, len(seqs) + 1))

    # The amendment's file landed under the amendment prefix, and the run
    # completed — hydration resolved the manifest under the amendment prefix.
    # Under the pre-fix derivation the manifest-listed file would be fetched from
    # captures/{capture_id}/stills/amend-wall.jpg, which does not exist, and the
    # run would fail during stage hydration.
    assert deps.storage.get_bytes(f"captures/{capture_id}/amendments/amd-join-1/stills/amend-wall.jpg") == blob
    with pytest.raises(OSError):
        deps.storage.get_bytes(f"captures/{capture_id}/stills/amend-wall.jpg")


def _register_extra_still(root: Path, client: TestClient, app, capture_id: str, name: str, image_id: str) -> bytes:
    """One still beyond the fixture manifest, carrying the meta the image lookup reads."""
    blob = f"extra-still-{name}-bytes".encode()
    item = {
        "path": f"stills/{name}.jpg",
        "bytes": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "md5": base64.b64encode(hashlib.md5(blob).digest()).decode(),
        "role": "still",
        "contentType": "image/jpeg",
        "meta": {
            "still": {
                "id": image_id,
                "purpose": "meter_close",
                "img": f"stills/{name}.jpg",
                "w": 2,
                "h": 2,
                "timestamp": 0,
                "orientation": 1,
            }
        },
    }
    registered = client.post(f"/v1/captures/{capture_id}/files", json={"files": [item]})
    assert registered.status_code == 200, registered.text
    upload = registered.json()["files"][0]["upload"]
    put = TestClient(app).put(upload["url"], content=blob, headers=upload.get("headers") or {})
    assert put.status_code == 200, put.text
    committed = client.post(
        f"/v1/captures/{capture_id}/files:commit", json={"files": [{"path": item["path"], "sha256": item["sha256"]}]}
    )
    assert committed.status_code == 200, committed.text
    return blob


def _confirm(client: TestClient, capture_id: str, field: str, kind: str, value: str, image_id: str, at: str):
    return client.post(
        f"/v1/captures/{capture_id}/confirmations",
        json={"fieldId": field, "kind": kind, "value": value, "imageId": image_id, "clientAt": at},
    )


def test_confirmation_before_reads_reaches_the_composed_result(journey) -> None:
    """A confirmation accepted before reads exist is persisted, applied when the
    reads stage creates its record, and served in the composed result — the
    client never re-sends an already-accepted answer."""
    root, packet, client, app = journey
    deps = app.state.deps
    capture_id, raw = _open_front_door(root, packet, client, app, "join-confirm-early")

    # The capture is still open (uploading) when the device confirms the meter.
    _register_extra_still(root, client, app, capture_id, "early-meter", "early-1")
    accepted = _confirm(client, capture_id, "meter_number", "corrected", "31415", "early-1", "2026-10-15T00:00:00Z")
    assert accepted.status_code == 201, accepted.text
    first_id = accepted.json()["confirmationId"]

    # The answer persists per field before any run exists.
    listed = client.get(f"/v1/captures/{capture_id}/confirmations").json()
    fields = [row["fieldId"] for row in listed["confirmations"]]
    assert fields == ["meter_number"]
    assert listed["confirmations"][0]["value"] == "31415"

    finalized = _finalize(client, capture_id, raw, "join-confirm-early")
    drain_finalize(deps)
    run = deps.state.get_run(finalized["runId"])
    assert run.status == "done"

    # The composed result carries the early answer.
    served = client.get(f"/v1/captures/{capture_id}/result").json()
    assert served["meter"]["value"] == "31415"
    assert served["meter"]["status"] == "auto_filled"

    # Replaying the same answer is idempotent — same confirmation, one event, no
    # new run, no recomposition.
    replayed = _confirm(client, capture_id, "meter_number", "corrected", "31415", "early-1", "2026-10-15T00:00:00Z")
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["confirmationId"] == first_id
    assert len(deps.state.runs_for_capture(capture_id)) == 1

    events = _flat(client, capture_id)
    confirmations = [e for e in events if e["type"] == "confirmation"]
    assert [e["data"]["confirmationId"] for e in confirmations] == [first_id]
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(set(seqs)) and seqs == list(range(1, len(seqs) + 1))


def test_correction_after_the_result_recomposes_the_served_payload(journey) -> None:
    """A late correction publishes the acceptance event and the recomposed result:
    the run's criteria and result stages re-execute through the normal lifecycle
    (one run, the same run id), the served meter carries the corrected value, and
    replaying the correction neither runs anything new nor changes the payload."""
    root, packet, client, app = journey
    deps = app.state.deps
    capture_id, raw = _open_front_door(root, packet, client, app, "join-confirm-late")
    accepted = _finalize(client, capture_id, raw, "join-confirm-late")
    run_id = accepted["runId"]
    drain_finalize(deps)
    assert deps.state.get_run(run_id).status == "done"

    baseline = client.get(f"/v1/captures/{capture_id}/result").json()
    assert baseline["meter"]["value"] != "777"
    base_verdicts = len([e for e in _flat(client, capture_id) if e["type"] == "verdict_ready"])

    image_id = packet["stills"][0]["id"]
    corrected = _confirm(client, capture_id, "meter_number", "corrected", "777", image_id, "2026-10-15T00:01:00Z")
    assert corrected.status_code == 201, corrected.text

    # One run, re-driven in place: the recomposed result serves the correction.
    runs = deps.state.runs_for_capture(capture_id)
    assert len(runs) == 1 and runs[0].run_id == run_id and runs[0].status == "done"
    served = client.get(f"/v1/captures/{capture_id}/result").json()
    assert served["meter"]["value"] == "777"
    assert served["meter"]["status"] == "auto_filled"

    events = _flat(client, capture_id)
    confirmations = [e for e in events if e["type"] == "confirmation"]
    assert [e["data"]["confirmationId"] for e in confirmations] == [corrected.json()["confirmationId"]]
    # The recomposition is visible: the run's result re-published its terminal.
    assert len([e for e in events if e["type"] == "verdict_ready"]) == base_verdicts + 1
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(set(seqs)) and seqs == list(range(1, len(seqs) + 1))

    # Replaying the same correction changes nothing further.
    replayed = _confirm(client, capture_id, "meter_number", "corrected", "777", image_id, "2026-10-15T00:01:00Z")
    assert replayed.status_code == 200, replayed.text
    assert client.get(f"/v1/captures/{capture_id}/result").json()["meter"]["value"] == "777"
    events_after = _flat(client, capture_id)
    assert len([e for e in events_after if e["type"] == "verdict_ready"]) == base_verdicts + 1
    assert [e["data"]["confirmationId"] for e in events_after if e["type"] == "confirmation"] == [
        corrected.json()["confirmationId"]
    ]


def test_a_correction_after_the_original_deadline_gets_its_own_budget(journey) -> None:
    """The run's deadline measured its first pass. A correction accepted after that
    deadline re-opens the run; without a fresh budget the next tick hard-stops it
    and the capture falls to manual review with the old result still served."""
    root, packet, client, app = journey
    deps = app.state.deps
    capture_id, raw = _open_front_door(root, packet, client, app, "join-confirm-expired")
    run_id = _finalize(client, capture_id, raw, "join-confirm-expired")["runId"]
    drain_finalize(deps)
    run = deps.state.get_run(run_id)
    assert run.status == "done"
    failure_before = run.failure_code
    # The original 20-minute deadline has passed by the time the correction lands.
    run.deadline_at = "2000-01-01T00:00:00+00:00"
    deps.state.put_run(run)
    status_before = deps.state.get_capture(capture_id).status

    image_id = packet["stills"][0]["id"]
    corrected = _confirm(client, capture_id, "meter_number", "corrected", "4242", image_id, "2026-10-15T00:02:00Z")
    assert corrected.status_code == 201, corrected.text

    rerun = deps.state.get_run(run_id)
    assert rerun.status == "done", (rerun.status, rerun.failure_code)
    assert rerun.failure_code == failure_before
    assert deps.state.get_capture(capture_id).status == status_before
    assert client.get(f"/v1/captures/{capture_id}/result").json()["meter"]["value"] == "4242"
