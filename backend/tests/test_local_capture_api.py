"""The local capture API launcher (`scripts/local_capture_api.py`) over a real socket.

`make local-api` must give a reviewer, the iOS simulator and
`tools/hunter-local-ingest` a working capture API on 127.0.0.1 from one command.
Every capture-to-result proof before this packet ran in process —
`scripts/capture_flow_e2e.py` drives `fastapi.testclient.TestClient` and calls
`drain_finalize` by hand — so these tests start the launcher as a subprocess on
an ephemeral loopback port and speak HTTP with `httpx`. No TestClient, no
in-process ASGI transport, and no `drain_finalize`: the run reaches a terminal
status because `start_run` drives the orchestrator inline.

The journey is the committed `data/fixtures/packets/mini-arkit` packet through
the documented front door: create, register, PUT to the signed upload URLs,
commit, finalize, long-poll events to a terminal status, read the result and the
reviewer page. The negative cases pin the auth and error boundaries a real
client hits: 401 without or with a wrong bearer, a 4xx on a malformed finalize,
`404 result_not_ready` on the reviewer page before any run, and 403 for the app
token on `/review`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "local_capture_api.py"
PACKET = ROOT / "data" / "fixtures" / "packets" / "mini-arkit"

TERMINAL_CAPTURE_STATUSES = {"manual_review", "complete", "failed", "needs_views", "expired"}
SESSION_KEYS = {"baseUrl", "appToken", "reviewerToken", "pid", "gitSha", "stageStubs", "startedAt"}


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
    """The launcher as a subprocess, plus the session it promised on disk."""

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


def _start_server(session_dir: Path, log_path: Path, extra_args: list[str] | None = None) -> subprocess.Popen:
    port = _free_port()
    log = log_path.open("w")
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
            *(extra_args or []),
        ],
        cwd=ROOT,
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
                    return proc
        except httpx.HTTPError:
            time.sleep(0.25)
    proc.terminate()
    pytest.fail(f"local capture API never got healthy:\n{log_path.read_text()}")


@pytest.fixture(scope="module")
def server(tmp_path_factory) -> LocalServer:
    session_dir = tmp_path_factory.mktemp("local-api")
    log_path = session_dir / "server.log"
    proc = _start_server(session_dir, log_path)
    try:
        yield LocalServer(session_dir, _port_from_session(session_dir), proc, log_path)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


def _port_from_session(session_dir: Path) -> int:
    session = json.loads((session_dir / "session.json").read_text())
    return int(session["baseUrl"].split(":")[-1].split("/")[0])


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


def _create_capture(client: httpx.Client, packet: dict, idempotency: str) -> dict:
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
        headers={"Idempotency-Key": idempotency},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "uploading"
    return response.json()


def _drive_to_terminal(client: httpx.Client, workdir: Path, packet_id: str) -> tuple[dict, str]:
    """The full capture journey over TCP. Returns (capture body, run id)."""
    root, packet = _copy_packet(workdir, packet_id)
    created = _create_capture(client, packet, packet["packetId"])
    capture_id = created["captureId"]

    files = _sealed_files(root, packet)
    registered = client.post(f"/captures/{capture_id}/files", json={"files": files})
    assert registered.status_code == 200, registered.text
    assert len(registered.json()["files"]) == len(files)

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

    # Long-poll events to a terminal status: the orchestrator drives inline, so
    # a real server must get there with nobody calling drain_finalize.
    after = 0
    deadline = time.monotonic() + 180
    status = None
    events = []
    while time.monotonic() < deadline:
        poll = client.get(f"/captures/{capture_id}/events", params={"after": after, "wait": 5})
        assert poll.status_code == 200, poll.text
        body = poll.json()
        status = body["status"]
        events.extend(body["events"])
        after = body["next"]
        if status in TERMINAL_CAPTURE_STATUSES:
            break
    assert status == "manual_review", f"expected a terminal manual_review, got {status}"
    # The run's stage events reach the capture event log the device long-polls.
    assert any((event.get("data") or {}).get("runId") == run_id for event in events), [
        event.get("type") for event in events
    ]
    return created, run_id


def test_local_capture_api_journey(server: LocalServer, tmp_path: Path) -> None:
    session = server.session

    # The session contract the iOS run and later packets read — pinned verbatim.
    assert set(session) == SESSION_KEYS, session
    assert session["baseUrl"] == server.base + "/v1"
    assert session["stageStubs"] is True
    assert session["pid"] == server.proc.pid
    assert isinstance(session["gitSha"], str) and len(session["gitSha"]) >= 7
    mode = stat.S_IMODE((server.session_dir / "session.json").stat().st_mode)
    assert mode == 0o600, oct(mode)

    app_token, reviewer_token = session["appToken"], session["reviewerToken"]
    assert app_token != reviewer_token and len(app_token) >= 32 and len(reviewer_token) >= 32

    with server.client(app_token) as client:
        capture, run_id = _drive_to_terminal(client, tmp_path, "mini-arkit-journey")
        capture_id = capture["captureId"]

        result = client.get(f"/captures/{capture_id}/result")
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["runId"] == run_id
        assert payload["outcome"] is not None, payload

        # The app token is not a reviewer.
        review = client.get(f"/captures/{capture_id}/review")
        assert review.status_code == 403, review.text
        assert review.json()["errors"][0]["code"] == "forbidden"

    with server.client(reviewer_token) as reviewer:
        page = reviewer.get(f"/captures/{capture_id}/review")
        assert page.status_code == 200, page.text
        assert "text/html" in page.headers["content-type"]
        assert len(page.content) > 0


def test_result_before_finalize_is_the_not_ready_envelope(server: LocalServer, tmp_path: Path) -> None:
    """GET result before any run serves the 200 not-ready envelope, the shape
    `server/api/tests/test_result_boundary.py` pins; the 404 result_not_ready
    code is the reviewer page's answer before any run (reviewer bearer — the
    reviewer gate runs before the not-ready check, so an app bearer gets 403)."""
    root, packet = _copy_packet(tmp_path, "mini-arkit-resnotready")
    with server.client(server.session["appToken"]) as client:
        capture_id = _create_capture(client, packet, packet["packetId"])["captureId"]
        result = client.get(f"/captures/{capture_id}/result")
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["outcome"] is None and body["viewsNeeded"] == []
    with server.client(server.session["reviewerToken"]) as reviewer:
        review = reviewer.get(f"/captures/{capture_id}/review")
        assert review.status_code == 404, review.text
        assert review.json()["errors"][0]["code"] == "result_not_ready"


def test_malformed_finalize_is_refused_4xx(server: LocalServer, tmp_path: Path) -> None:
    root, packet = _copy_packet(tmp_path, "mini-arkit-malformed")
    with server.client(server.session["appToken"]) as client:
        capture_id = _create_capture(client, packet, packet["packetId"])["captureId"]
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
        assert errors and all({"code", "pointer", "message"} <= set(item) for item in errors)


def test_missing_and_wrong_bearer_get_401(server: LocalServer) -> None:
    with httpx.Client(base_url=f"{server.base}/v1", timeout=30) as anon:
        missing = anon.get("/captures/whatever/events")
        assert missing.status_code == 401, missing.text
        errors = missing.json()["errors"]
        assert errors and all({"code", "pointer", "message"} <= set(item) for item in errors)
        wrong = anon.get("/captures/whatever/events", headers={"Authorization": "Bearer " + "w" * 40})
        assert wrong.status_code == 401, wrong.text
        assert wrong.json()["errors"][0]["code"] == "unauthorized"


def test_tokens_never_reach_the_log(server: LocalServer) -> None:
    log = server.log()
    assert server.session["appToken"] not in log
    assert server.session["reviewerToken"] not in log
    # The baseUrl carries host and port only; the session file is the token carrier.
    assert server.session["appToken"] not in server.base
    assert "appToken" in (server.session_dir / "session.json").read_text()


def test_refuses_non_loopback_host(tmp_path: Path) -> None:
    log_path = tmp_path / "refused.log"
    with log_path.open("w") as log:
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--host",
                "0.0.0.0",
                "--port",
                str(_free_port()),
                "--session-dir",
                str(tmp_path),
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
    assert proc.returncode != 0
    assert "loopback" in log_path.read_text()
    assert not (tmp_path / "session.json").exists()


def _make_n_local_api(env: dict[str, str] | None = None) -> str:
    """Print the `make -n local-api` recipe from backend/ without executing it."""
    proc = subprocess.run(
        ["make", "-n", "local-api"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, **(env or {})},
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_make_local_api_without_port_passes_the_documented_default() -> None:
    # The documented flow (docs/local-capture-api.md) is bare `make local-api`
    # on port 8000; the recipe must not hand the launcher a bare --port.
    assert "--port 8000" in _make_n_local_api()


def test_make_local_api_honors_an_explicit_port_override() -> None:
    stdout = _make_n_local_api({"PORT": "8123"})
    assert "--port 8123" in stdout
    assert "--port 8000" not in stdout


def test_make_local_api_with_empty_port_env_still_gets_the_default() -> None:
    # An exported-but-empty PORT would otherwise expand to a bare `--port`.
    assert "--port 8000" in _make_n_local_api({"PORT": ""})


def test_launcher_refuses_an_empty_port_loudly(tmp_path: Path) -> None:
    log_path = tmp_path / "empty-port.log"
    with log_path.open("w") as log:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--host", "127.0.0.1", "--port="],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
    assert proc.returncode != 0
    log_text = log_path.read_text()
    assert "refusing an empty --port" in log_text
    assert "8000" in log_text  # the refusal names the documented default
    assert not (tmp_path / "session.json").exists()
