"""Every state the app renders, reached over a real HTTP socket.

`scripts/local_capture_api.py` with `HSS_LOCAL_OUTCOME` scripts each terminal
state the iOS app renders while photo processing runs; this file drives the
real server (the launcher as a subprocess on an ephemeral loopback port) into
each one and checks the status, the outcome kind and the fields the screen
needs — the checks `tests/test_local_outcome_states.py` pins in process, replayed
over the wire the app uses. No TestClient, no in-process ASGI transport.

The journey per state is the documented front door: create, register, PUT to
the signed upload URLs, commit, finalize the committed mini-arkit packet,
long-poll events to a terminal status, then read the result (and the reviewer
page). Each screen's needs come from the app side:
`ios/HouseScanKit/Sources/HouseScanKit/Processing/PhotoProcessingStatus.swift`
and `ios/HouseScan/UI/Copy/ProcessingCopy.swift`.

  processing          events stream stage events while the run works
                      (`HSS_LOCAL_STAGE_DELAY_S` keeps it visible)  -> "Processing your scan"
  eligible            status complete, outcome.kind eligible, a proposed
                      recommendedPlacement                          -> "A possible battery spot"
  needs_more_photos   status needs_views, one viewsNeeded prompt    -> "One more look needed"
  manual_review       status manual_review                          -> "Needs an installer's review"
  not_eligible        status complete, outcome.kind not_eligible
                      (needs SHOW_REJECT_TO_HOMEOWNER=1)            -> "No possible spot found"
  failed              events end failed: the failing stage and a
                      plain message                                 -> "Processing didn't finish"
  refused             a 4xx with its code for a bad packet, 401 for
                      a bad token                                   -> "The processing service refused this scan"
  not ready           404 result_not_ready on the reviewer page
                      before any run; the result answers with the
                      not-ready envelope                            -> "answerNotReady" / not-ready envelope

The committed fixtures under `tests/fixtures/app_states/<state>/` are the
served payloads the outcome-states packet pinned in process; the terminal-state
tests here cross-check the served status and outcome kind against them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "local_capture_api.py"
PACKET = ROOT / "data" / "fixtures" / "packets" / "mini-arkit"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "app_states"

TERMINAL_CAPTURE_STATUSES = {"manual_review", "complete", "failed", "needs_views", "expired"}


def _content_type(role: str) -> str:
    if role == "stream":
        return "application/gzip"
    if role in {"features", "depth", "confidence", "mesh"}:
        return "application/octet-stream"
    return "image/jpeg"


def _sealed_files(root: Path, packet: dict) -> list[dict]:
    """Register bodies with the hashes the app computes from the sealed files."""
    files = []
    for entry in packet["files"]:
        data = (root / entry["path"]).read_bytes()
        role = entry.get("role", "still")
        files.append(
            {
                "path": entry["path"],
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "md5": base64.b64encode(hashlib.md5(data).digest()).decode(),
                "role": role,
                "contentType": _content_type(role),
            }
        )
    return files


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalServer:
    """The launcher as a subprocess with a scripted outcome, plus its session."""

    def __init__(self, session_dir: Path, port: int, proc: subprocess.Popen, log_path: Path):
        self.session_dir = session_dir
        self.port = port
        self.proc = proc
        self.log_path = log_path
        self.session = json.loads((session_dir / "session.json").read_text())

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def client(self, token: str | None) -> httpx.Client:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx.Client(base_url=f"{self.base}/v1", headers=headers, timeout=30)

    def log(self) -> str:
        return self.log_path.read_text()


def _start_server(session_dir: Path, log_path: Path, extra_env: dict[str, str] | None = None) -> LocalServer:
    port = _free_port()
    log = log_path.open("w")
    env = {**os.environ, **(extra_env or {})}
    proc = subprocess.Popen(
        [
            sys.executable,
            str(SCRIPT),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--session-dir",
            str(session_dir),
            "--health-timeout",
            "90",
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log.close()
    # The launcher writes session.json before binding, so wait for healthz.
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"local capture API exited early with {proc.returncode}:\n{log_path.read_text()}")
        try:
            with httpx.Client(timeout=2) as probe:
                if probe.get(f"http://127.0.0.1:{port}/v1/healthz").status_code == 200:
                    return LocalServer(session_dir, port, proc, log_path)
        except httpx.HTTPError:
            time.sleep(0.25)
    proc.terminate()
    pytest.fail(f"local capture API never got healthy:\n{log_path.read_text()}")


def _stop(server: LocalServer) -> None:
    server.proc.terminate()
    try:
        server.proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        server.proc.kill()


@pytest.fixture(scope="module")
def outcome_server(tmp_path_factory) -> LocalServer:
    """One server for the refused and not-ready walks (default manual_review)."""
    session_dir = tmp_path_factory.mktemp("local-api-refused")
    server = _start_server(session_dir, session_dir / "server.log")
    try:
        yield server
    finally:
        _stop(server)


def _outcome_server(outcome: str, tmp_path_factory, show_reject: bool = False) -> LocalServer:
    session_dir = tmp_path_factory.mktemp(f"local-api-{outcome}")
    env = {"HSS_LOCAL_OUTCOME": outcome}
    if show_reject:
        env["SHOW_REJECT_TO_HOMEOWNER"] = "1"
    return _start_server(session_dir, session_dir / "server.log", env)


def _copy_packet(workdir: Path, packet_id: str) -> tuple[Path, dict]:
    """A private copy of the committed packet, retagged with a unique packet id.

    The create route pins Idempotency-Key to the packet id, so two journeys on
    one server must not share one; finalize requires the packet bytes to carry
    the same id, so the copy is rewritten before any finalize.
    """
    root = workdir / "packet"
    subprocess.run(["cp", "-R", str(PACKET), str(root)], check=True)
    packet = json.loads((root / "packet.json").read_text())
    packet["packetId"] = packet_id
    (root / "packet.json").write_text(json.dumps(packet, indent=2) + "\n")
    return root, packet


def _create_capture(client: httpx.Client, packet: dict) -> dict:
    session = packet["session"]
    response = client.post(
        "/captures",
        json={
            "packetId": packet["packetId"],
            "formatVersion": packet["formatVersion"],
            "tier": session["tier"],
            "flow": "guided",
            "device": {
                "model": session["deviceModel"],
                "systemVersion": session["systemVersion"],
                "appVersion": session["appVersion"],
            },
        },
        headers={"Idempotency-Key": packet["packetId"]},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "uploading"
    return response.json()


def _register_upload_commit(client: httpx.Client, root: Path, packet: dict, capture_id: str) -> None:
    files = _sealed_files(root, packet)
    registered = client.post(f"/captures/{capture_id}/files", json={"files": files})
    assert registered.status_code == 200, registered.text

    with httpx.Client(timeout=60) as uploads:
        for body, target in zip(files, registered.json()["files"], strict=True):
            put = uploads.put(
                target["upload"]["url"],
                content=(root / body["path"]).read_bytes(),
                headers=target["upload"].get("headers") or {},
            )
            assert put.status_code == 200, f"{put.status_code} for {body['path']}: {put.text}"

    commit = client.post(
        f"/captures/{capture_id}/files:commit",
        json={"files": [{"path": item["path"], "sha256": item["sha256"]} for item in files]},
    )
    assert commit.status_code == 200, commit.text
    assert not commit.json().get("notFound"), commit.text
    assert not commit.json().get("mismatch"), commit.text


def _finalize(client: httpx.Client, root: Path, packet: dict, capture_id: str, raw: bytes | None = None) -> str:
    if raw is None:
        raw = (root / "packet.json").read_bytes()
    finalized = client.post(
        f"/captures/{capture_id}/finalize",
        content=raw,
        headers={
            "Idempotency-Key": packet["packetId"],
            "Content-Type": "application/json",
            "X-Packet-Sha256": hashlib.sha256(raw).hexdigest(),
        },
    )
    assert finalized.status_code == 202, finalized.text
    run_id = finalized.json()["runId"]
    assert run_id, finalized.text
    return run_id


def _poll_events(client: httpx.Client, capture_id: str, deadline_s: float = 180) -> tuple[str, list[dict]]:
    """Long-poll to a terminal status. Returns (status, all events seen)."""
    after = 0
    events: list[dict] = []
    deadline = time.monotonic() + deadline_s
    status = None
    while time.monotonic() < deadline:
        poll = client.get(f"/captures/{capture_id}/events", params={"after": after, "wait": 5})
        assert poll.status_code == 200, poll.text
        body = poll.json()
        status = body["status"]
        events.extend(body["events"])
        after = body["next"]
        if status in TERMINAL_CAPTURE_STATUSES:
            return status, events
    pytest.fail(f"events never reached a terminal status; last {status!r}")


def _events_of_type(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event.get("type") == kind]


def _drive_full(client: httpx.Client, workdir: Path, packet_id: str) -> tuple[dict, str, str, list[dict]]:
    """The whole journey to a terminal status. Returns (capture, capture_id, run_id, events)."""
    root, packet = _copy_packet(workdir, packet_id)
    created = _create_capture(client, packet)
    capture_id = created["captureId"]
    _register_upload_commit(client, root, packet, capture_id)
    run_id = _finalize(client, root, packet, capture_id)
    status, events = _poll_events(client, capture_id)
    return created, capture_id, run_id, events


def _result(client: httpx.Client, capture_id: str) -> dict:
    response = client.get(f"/captures/{capture_id}/result")
    assert response.status_code == 200, response.text
    return response.json()


def _fixture(state: str) -> dict:
    return json.loads((FIXTURES / state / "result.json").read_text())


# --- the terminal states the app renders --------------------------------------


@pytest.mark.parametrize(
    "outcome",
    ["eligible", "needs_more_photos", "manual_review"],
)
def test_terminal_state_serves_what_the_screen_needs(outcome: str, tmp_path_factory, tmp_path: Path) -> None:
    """Each scripted outcome lands the capture on its capture status, and the
    served result carries the fields that screen reads."""
    server = _outcome_server(outcome, tmp_path_factory)
    try:
        with server.client(server.session["appToken"]) as client:
            capture, capture_id, run_id, events = _drive_full(client, tmp_path, f"mini-arkit-{outcome}")

            result = _result(client, capture_id)
            expected = _fixture(outcome)
            assert result["status"] == expected["status"], result
            assert result["runId"] == run_id
            assert result["outcome"] is not None
            assert result["outcome"]["kind"] == expected["outcome"]["kind"]
            # The message is the service's words for the homeowner, shown verbatim.
            assert result["outcome"]["message"], result["outcome"]

            with server.client(server.session["reviewerToken"]) as reviewer:
                page = reviewer.get(f"/captures/{capture_id}/review")
                assert page.status_code == 200, page.text
                assert "text/html" in page.headers["content-type"]
                assert outcome.encode() in page.content, page.text[:400]
    finally:
        _stop(server)


def test_eligible_carries_a_proposed_placement(tmp_path_factory, tmp_path: Path) -> None:
    """'A possible battery spot': kind eligible worded as proposed, with the
    placement fields the AR box and the card need — still only a candidate."""
    server = _outcome_server("eligible", tmp_path_factory)
    try:
        with server.client(server.session["appToken"]) as client:
            capture, capture_id, run_id, events = _drive_full(client, tmp_path, "mini-arkit-eligible-placement")

            result = _result(client, capture_id)
            assert result["status"] == "complete", result
            outcome = result["outcome"]
            assert outcome["kind"] == "eligible", outcome
            # Worded as proposed: the service's own message says the placement
            # is scripted, not measured, so the answer stays provisional.
            assert "scripted" in outcome["message"], outcome["message"]
            placement = outcome["recommendedPlacement"]
            assert placement is not None, outcome
            assert placement["wallId"], placement
            assert placement["footprintM"]["w"] > 0 and placement["footprintM"]["h"] > 0, placement
            assert placement["boxSceneM"]["centerM"] and placement["boxSceneM"]["sizeM"], placement
            assert result["reviewUrl"], result
    finally:
        _stop(server)


def test_needs_more_photos_lists_the_views(tmp_path_factory, tmp_path: Path) -> None:
    """'One more look needed': status needs_views and at least one view whose
    prompt the screen shows verbatim."""
    server = _outcome_server("needs_more_photos", tmp_path_factory)
    try:
        with server.client(server.session["appToken"]) as client:
            capture, capture_id, run_id, events = _drive_full(client, tmp_path, "mini-arkit-needs-more")

            result = _result(client, capture_id)
            assert result["status"] == "needs_views", result
            outcome = result["outcome"]
            assert outcome["kind"] == "needs_more_photos", outcome
            views = result["viewsNeeded"]
            assert views, "the needs-more-photos screen needs the requested views"
            first = views[0]
            assert first["id"], first
            assert first["kind"] == "band", first
            assert first["prompt"]["title"] and first["prompt"]["body"], first
            assert first["why"], first
    finally:
        _stop(server)


def test_not_eligible_shown_to_the_homeowner(tmp_path_factory, tmp_path: Path) -> None:
    """'No possible spot found' needs SHOW_REJECT_TO_HOMEOWNER=1: kind
    not_eligible with the service's message, capture complete."""
    server = _outcome_server("not_eligible", tmp_path_factory, show_reject=True)
    try:
        with server.client(server.session["appToken"]) as client:
            capture, capture_id, run_id, events = _drive_full(client, tmp_path, "mini-arkit-not-eligible")

            result = _result(client, capture_id)
            assert result["status"] == "complete", result
            outcome = result["outcome"]
            assert outcome["kind"] == "not_eligible", outcome
            assert outcome["message"], outcome
            assert outcome["recommendedPlacement"] is None, outcome
    finally:
        _stop(server)


def test_not_eligible_without_show_reject_lands_manual_review(tmp_path_factory, tmp_path: Path) -> None:
    """With the reject hidden the capture must not show 'No possible spot':
    it lands on the installer-review screen instead, as the outcome table says."""
    server = _outcome_server("not_eligible", tmp_path_factory, show_reject=False)
    try:
        with server.client(server.session["appToken"]) as client:
            capture, capture_id, run_id, events = _drive_full(client, tmp_path, "mini-arkit-not-eligible-hidden")

            result = _result(client, capture_id)
            assert result["status"] == "manual_review", result
            assert result["outcome"]["kind"] == "manual_review", result["outcome"]
    finally:
        _stop(server)


def test_failed_names_the_stage_and_the_plain_message(tmp_path_factory, tmp_path: Path) -> None:
    """'Processing didn't finish': the events end failed, naming the failing
    stage and carrying a plain message, and the result answers with status failed."""
    server = _outcome_server("failed", tmp_path_factory)
    try:
        with server.client(server.session["appToken"]) as client:
            capture, capture_id, run_id, events = _drive_full(client, tmp_path, "mini-arkit-failed")

            poll = client.get(f"/captures/{capture_id}/events", params={"after": 0})
            body = poll.json()
            assert body["status"] == "failed", body

            failed_events = _events_of_type(body["events"], "failed")
            assert failed_events, "a failed run must publish its failed event"
            data = failed_events[-1]["data"]
            assert data.get("code"), data
            assert data.get("message"), "the failed screen needs a plain message"
            assert data.get("next") == "none", data

            stage_failures = [
                event
                for event in _events_of_type(body["events"], "stage")
                if event["data"].get("status") == "failed"
            ]
            assert stage_failures, "the events must name the stage that failed"
            assert stage_failures[-1]["data"].get("stage"), stage_failures[-1]

            result = _result(client, capture_id)
            assert result["status"] == "failed", result
            expected = _fixture("failed")
            assert (result.get("outcome") or {}).get("kind", "manual_review") == expected["outcome"]["kind"]
    finally:
        _stop(server)


# --- the state before any answer ---------------------------------------------


def test_processing_streams_stage_events_while_the_run_works(tmp_path_factory, tmp_path: Path) -> None:
    """'Processing your scan': with HSS_LOCAL_STAGE_DELAY_S pacing the stubs, a
    mid-run events long-poll shows the capture still processing and the stage
    events the screen's working state stands on."""
    session_dir = tmp_path_factory.mktemp("local-api-processing")
    server = _start_server(session_dir, session_dir / "server.log", {"HSS_LOCAL_STAGE_DELAY_S": "2"})
    try:
        with server.client(server.session["appToken"]) as client:
            root, packet = _copy_packet(tmp_path, "mini-arkit-processing")
            created = _create_capture(client, packet)
            capture_id = created["captureId"]
            _register_upload_commit(client, root, packet, capture_id)
            run_id = _finalize(client, root, packet, capture_id)

            # Twelve stubs, two seconds each: the run is still working here.
            poll = client.get(f"/captures/{capture_id}/events", params={"after": 0, "wait": 2})
            assert poll.status_code == 200, poll.text
            body = poll.json()
            assert body["status"] == "processing", body
            stage_events = [
                event
                for event in _events_of_type(body["events"], "stage")
                if event["data"].get("runId") == run_id
                and event["data"].get("status") in {"pending", "running", "done"}
            ]
            assert stage_events, "the processing screen stands on the run's stage events"
            assert any(event["data"].get("stage") for event in stage_events), stage_events

            # The run still gets to its terminal answer with the delay on.
            status, events = _poll_events(client, capture_id)
            assert status == "manual_review", status
    finally:
        _stop(server)


# --- the refusals and the not-ready answer -----------------------------------


def test_refused_bad_packet_is_a_4xx_with_its_code(outcome_server: LocalServer, tmp_path: Path) -> None:
    """'The processing service refused this scan': a malformed finalize is a 4xx
    whose errors name a code — sending the same thing again won't change it."""
    server = outcome_server
    root, packet = _copy_packet(tmp_path, "mini-arkit-refused-packet")
    with server.client(server.session["appToken"]) as client:
        capture_id = _create_capture(client, packet)["captureId"]
        raw = b"{not json at all"
        response = client.post(
            f"/captures/{capture_id}/finalize",
            content=raw,
            headers={
                "Idempotency-Key": packet["packetId"],
                "Content-Type": "application/json",
                "X-Packet-Sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        assert 400 <= response.status_code < 500, response.text
        errors = response.json()["errors"]
        assert errors and all({"code", "pointer", "message"} <= set(item) for item in errors), errors


def test_refused_bad_token_is_401_with_its_code(outcome_server: LocalServer) -> None:
    """A wrong bearer never reaches the capture: 401 with the error envelope."""
    server = outcome_server
    with httpx.Client(base_url=f"{server.base}/v1", timeout=30) as anon:
        missing = anon.get("/captures/whatever/events")
        assert missing.status_code == 401, missing.text
        errors = missing.json()["errors"]
        assert errors and errors[0]["code"], errors
        wrong = anon.get("/captures/whatever/events", headers={"Authorization": "Bearer " + "w" * 40})
        assert wrong.status_code == 401, wrong.text
        assert wrong.json()["errors"][0]["code"] == errors[0]["code"], wrong.text


def test_not_ready_result_and_reviewer_page(outcome_server: LocalServer, tmp_path: Path) -> None:
    """'answerNotReady': before any run the result answers with the not-ready
    envelope (outcome none), and the reviewer page is 404 result_not_ready."""
    server = outcome_server
    root, packet = _copy_packet(tmp_path, "mini-arkit-not-ready")
    with server.client(server.session["appToken"]) as client:
        capture_id = _create_capture(client, packet)["captureId"]
        result = client.get(f"/captures/{capture_id}/result")
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["outcome"] is None and body["viewsNeeded"] == [], body
        assert body["status"] == "uploading", body
    with server.client(server.session["reviewerToken"]) as reviewer:
        review = reviewer.get(f"/captures/{capture_id}/review")
        assert review.status_code == 404, review.text
        assert review.json()["errors"][0]["code"] == "result_not_ready", review.text
