"""Upload volume routes over a real TCP socket, the way the phone reaches them.

Every other upload test runs through ``TestClient``, which never drops a
connection and never lets uvicorn's event loop multiplex requests, so it cannot
see what an interrupted PUT, a retry on the same signed URL, or concurrent PUTs
racing open event long-polls do to the volume. ``CaptureUploader`` retries a
dropped PUT on the *same* signed URL (a transport error is ``Transient``, only a
403/410 goes back to register for a fresh one), resends sealed bytes once on a
400, and honors ``Retry-After`` on a 429, so the server side of those rules is
what these tests pin:

- an aborted PUT commits nothing and leaves no temp file behind;
- a PUT that arrives while an earlier attempt is still writing cannot corrupt
  the committed object (`prepare_tmp` used to hand both attempts the same
  `{key}.uploading` path — the Greptile finding on #247 about parallel uploads);
- a replay of a completed PUT succeeds;
- a PUT after the token expired is 410 and a fresh URL commits;
- a wrong Content-MD5, a wrong body, a short body and a wrong Content-Length
  are refused with nothing committed;
- 8 concurrent PUTs alongside 2 open event long-polls all finish inside a
  bound, with 429s retried the way the app retries them;
- an Authorization header on a storage PUT is ignored, never required: the
  app's `ScopedCaptureHTTP` refuses to send one (signed headers only).

The server runs as a subprocess bound to 127.0.0.1 on an ephemeral port with
memory state and local volume storage; no cloud service, no real data.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from server.api.keys import upload_key
from server.common.backends.storage_volume import mint_token

TOKEN = "d" * 40
SECRET = "h" * 32
BACKEND_DIR = Path(__file__).resolve().parents[3]

RUNNER = """
import json, sys
import uvicorn
from server.api.app import create_app
from server.common.config import Settings

settings = Settings(**json.loads(sys.argv[1]))
app = create_app(settings=settings)
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning", access_log=False)
"""


class Server:
    """One create_app under uvicorn on 127.0.0.1, plus its volume root."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "vol"
        port_socket = socket.socket()
        port_socket.bind(("127.0.0.1", 0))
        self.port = port_socket.getsockname()[1]
        port_socket.close()
        settings = {
            "SMP_ENVIRONMENT": "dev",
            "HSS_AUTH_MODE": "static",
            "CAPTURE_API_TOKENS": f"device:{TOKEN}",
            "UPLOAD_URL_HMAC_SECRET": SECRET,
            "STORAGE_BACKEND": "volume",
            "STATE_BACKEND": "memory",
            "VOLUME_ROOT": str(self.root),
            "CAPTURE_API_PUBLIC_URL": f"http://127.0.0.1:{self.port}",
            "HSS_INGEST_HOOKS": "null",
            "VISION_EXTERNAL_CALLS": "on",
            "GIT_SHA": "0123456789abcdef",
            "SMP_SERVICE_NAME": "capture-api",
        }
        self.process = subprocess.Popen(
            [sys.executable, "-c", RUNNER, json.dumps(settings), str(self.port)],
            cwd=str(BACKEND_DIR),
            env={"PYTHONPATH": str(BACKEND_DIR), "PATH": "/usr/bin:/bin"},
        )
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{self.port}",
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=httpx.Timeout(15.0),
            limits=httpx.Limits(max_connections=16),
        )
        deadline = time.monotonic() + 30
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                if self.client.get("/v1/healthz").status_code == 200:
                    return
            except httpx.TransportError as error:
                last_error = error
            time.sleep(0.1)
        self.close()
        raise RuntimeError(f"the test server never became healthy: {last_error}")

    def close(self) -> None:
        self.client.close()
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


@pytest.fixture
def server(tmp_path):
    live = Server(tmp_path)
    yield live
    live.close()


def _md5_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.md5(data).digest()).decode()


def _pattern(size: int, seed: int = 0) -> bytes:
    return bytes((index * 31 + seed * 7 + 11) % 251 for index in range(size))


def _create_capture(server: Server, packet_id: str) -> str:
    created = server.client.post(
        "/v1/captures",
        json={
            "packetId": packet_id,
            "formatVersion": "0.4",
            "tier": "photo_only",
            "flow": "photo",
            "device": {"model": "iPhone", "systemVersion": "18", "appVersion": "1"},
        },
        headers={"Idempotency-Key": packet_id},
    )
    assert created.status_code == 201, created.text
    return created.json()["captureId"]


def _register(server: Server, capture_id: str, path: str, data: bytes) -> dict:
    """The app's register call; returns the signed PUT target it would use."""
    registered = server.client.post(
        f"/v1/captures/{capture_id}/files",
        json={
            "files": [
                {
                    "path": path,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "md5": _md5_b64(data),
                    "role": "still",
                    "contentType": "image/jpeg",
                }
            ]
        },
    )
    assert registered.status_code == 200, registered.text
    entry = registered.json()["files"][0]
    assert entry["path"] == path and entry["state"] == "pending"
    target = entry["upload"]
    assert target["method"] == "PUT" and target["url"]
    return target


def _put(server: Server, target: dict, data: bytes, **extra_headers: str) -> httpx.Response:
    headers = dict(target["headers"])
    headers.update(extra_headers)
    return server.client.put(target["url"], content=data, headers=headers)


def _commit(server: Server, capture_id: str, path: str, data: bytes) -> dict:
    committed = server.client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": path, "sha256": hashlib.sha256(data).hexdigest()}]},
    )
    assert committed.status_code == 200, committed.text
    return committed.json()


def _stored(server: Server, capture_id: str, path: str) -> bytes:
    object_path = server.root / "uploads" / capture_id / path
    assert object_path.is_file(), f"no committed object at {object_path}"
    return object_path.read_bytes()


def _leftover_tmp(server: Server) -> list[str]:
    return [str(path) for path in server.root.rglob("*") if path.is_file() and path.name.endswith(".uploading")]


class RawPut:
    """A PUT sent by hand, so the test can stop mid-body or replay it while open."""

    def __init__(self, port: int, url: str, content_type: str, md5_b64: str, total: int) -> None:
        self.path = urlparse(url).path
        self.total = total
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.head = (
            f"PUT {self.path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-MD5: {md5_b64}\r\n"
            f"Content-Length: {total}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()

    def send_head(self) -> None:
        self.sock.sendall(self.head)

    def send_bytes(self, data: bytes) -> None:
        self.sock.sendall(data)

    def abort(self) -> None:
        """Close with an RST, as a phone does when it gives up on a stalled PUT."""
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        self.sock.close()

    def response(self) -> tuple[int, bytes]:
        """Send nothing more (the peer half-closes) and read the whole response."""
        try:
            self.sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        chunks: list[bytes] = []
        while True:
            try:
                chunk = self.sock.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        self.sock.close()
        raw = b"".join(chunks)
        head, _, body = raw.partition(b"\r\n\r\n")
        status = int(head.split(b"\r\n")[0].split()[1])
        return status, body


def _wait_for_tmp_to_settle(server: Server, seconds: float = 5.0) -> list[str]:
    """Give the server a moment to notice a dropped connection, then report leftovers."""
    deadline = time.monotonic() + seconds
    leftover = _leftover_tmp(server)
    while leftover and time.monotonic() < deadline:
        time.sleep(0.1)
        leftover = _leftover_tmp(server)
    return leftover


def test_aborted_put_commits_nothing_and_a_retry_to_the_same_url_succeeds(server: Server) -> None:
    capture_id = _create_capture(server, "tcp-abort-1")
    data = _pattern(256 * 1024)
    target = _register(server, capture_id, "photos/a.jpg", data)
    md5 = _md5_b64(data)

    aborted = RawPut(server.port, target["url"], "image/jpeg", md5, len(data))
    aborted.send_head()
    aborted.send_bytes(data[: len(data) // 2])
    # Let the handler start and settle into its write loop first, so the abort
    # cannot win a race against request dispatch.
    time.sleep(0.5)
    aborted.abort()

    leftover = _wait_for_tmp_to_settle(server)
    assert leftover == [], f"an aborted PUT left temp files behind: {leftover}"
    answer = _commit(server, capture_id, "photos/a.jpg", data)
    assert answer["notFound"] == ["photos/a.jpg"], "an aborted PUT must not commit anything"

    # The app's rule: a transport error is transient, so the same signed URL is retried.
    retried = _put(server, target, data)
    assert retried.status_code == 200, retried.text
    answer = _commit(server, capture_id, "photos/a.jpg", data)
    assert answer["committed"] == ["photos/a.jpg"]
    assert _stored(server, capture_id, "photos/a.jpg") == data


def test_a_put_while_an_earlier_attempt_is_still_writing_never_corrupts_the_object(server: Server) -> None:
    """The retry for a dropped PUT reuses the signed URL while the first attempt's
    handler can still be draining its body. Both attempts carry the same sealed
    bytes, so whichever commits, the object must be exactly those bytes."""
    capture_id = _create_capture(server, "tcp-overlap-1")
    data = _pattern(256 * 1024, seed=3)
    target = _register(server, capture_id, "photos/b.jpg", data)
    md5 = _md5_b64(data)

    first = RawPut(server.port, target["url"], "image/jpeg", md5, len(data))
    first.send_head()
    first.send_bytes(data[: len(data) // 2])
    # Let the first attempt's handler create its temp file and settle into its
    # write loop, so the overlap below is deterministic.
    time.sleep(0.5)

    second = _put(server, target, data)
    assert second.status_code == 200, second.text

    first.send_bytes(data[len(data) // 2 :])
    status, body = first.response()
    assert status == 200, f"the still-open first attempt must commit cleanly, got {status}: {body[:200]!r}"

    answer = _commit(server, capture_id, "photos/b.jpg", data)
    assert answer["committed"] == ["photos/b.jpg"], answer
    assert _stored(server, capture_id, "photos/b.jpg") == data, "the committed object is not the sealed bytes"
    assert _leftover_tmp(server) == []


def test_replaying_a_completed_put_succeeds(server: Server) -> None:
    capture_id = _create_capture(server, "tcp-replay-1")
    data = _pattern(64 * 1024, seed=5)
    target = _register(server, capture_id, "photos/c.jpg", data)

    first = _put(server, target, data)
    assert first.status_code == 200, first.text
    replay = _put(server, target, data)
    assert replay.status_code == 200, replay.text

    answer = _commit(server, capture_id, "photos/c.jpg", data)
    assert answer["committed"] == ["photos/c.jpg"]
    assert _stored(server, capture_id, "photos/c.jpg") == data


def test_put_after_the_url_expires_is_410_and_a_wrong_op_token_is_403(server: Server) -> None:
    capture_id = _create_capture(server, "tcp-expiry-1")
    data = _pattern(32 * 1024, seed=7)
    path = "photos/d.jpg"
    _register(server, capture_id, path, data)
    key = upload_key(capture_id, path)

    expired = mint_token(
        {"op": "put", "key": key, "md5": _md5_b64(data), "ct": "image/jpeg", "exp": int(time.time()) - 30},
        SECRET.encode(),
    )
    late = server.client.put(
        f"/v1/uploads/{expired}", content=data, headers={"Content-Type": "image/jpeg", "Content-MD5": _md5_b64(data)}
    )
    assert late.status_code == 410, late.text
    assert late.json()["errors"][0]["code"] == "token_expired"

    get_token = mint_token(
        {"op": "get", "key": key, "md5": "", "ct": "", "exp": int(time.time()) + 300}, SECRET.encode()
    )
    wrong_op = server.client.put(
        f"/v1/uploads/{get_token}",
        content=data,
        headers={"Content-Type": "image/jpeg", "Content-MD5": _md5_b64(data)},
    )
    assert wrong_op.status_code == 403, wrong_op.text

    answer = _commit(server, capture_id, path, data)
    assert answer["notFound"] == [path], "an expired-URL PUT must not commit anything"

    # The app's rule: a spent URL goes back to register, which mints a fresh one.
    # (A re-register inside the same second returns a byte-identical token — the
    # mint is deterministic at second granularity — which the app PUTs again.)
    fresh = _register(server, capture_id, path, data)
    retried = _put(server, fresh, data)
    assert retried.status_code == 200, retried.text
    assert _commit(server, capture_id, path, data)["committed"] == [path]
    assert _stored(server, capture_id, path) == data


def test_wrong_md5_wrong_body_short_body_and_wrong_length_are_refused(server: Server) -> None:
    capture_id = _create_capture(server, "tcp-digests-1")
    data = _pattern(48 * 1024, seed=9)
    path = "photos/e.jpg"
    target = _register(server, capture_id, path, data)
    md5 = _md5_b64(data)

    wrong_header = _put(server, target, data, **{"Content-MD5": _md5_b64(b"other bytes")})
    assert wrong_header.status_code == 400, wrong_header.text
    assert wrong_header.json()["errors"][0]["code"] == "bad_digest"

    wrong_content_type = server.client.put(
        target["url"], content=data, headers={"Content-Type": "image/png", "Content-MD5": md5}
    )
    assert wrong_content_type.status_code == 400, wrong_content_type.text

    wrong_body = _put(server, target, data[:-1] + bytes([data[-1] ^ 0xFF]))
    assert wrong_body.status_code == 400, wrong_body.text
    assert wrong_body.text == "BadDigest"

    short = RawPut(server.port, target["url"], "image/jpeg", md5, len(data))
    short.send_head()
    short.send_bytes(data[: len(data) - 1])
    # A body that ends before its declared length is a disconnect on the wire, so
    # the server may be unable to answer at all; if an answer is readable it must
    # not be a success. Either way nothing may commit.
    try:
        status, _body = short.response()
        assert status // 100 != 2, f"a short body must not be accepted, got {status}"
    except (OSError, IndexError, ValueError):
        pass  # the server dropped the connection instead of answering

    wrong_length = RawPut(server.port, target["url"], "image/jpeg", md5, len(data) - 1)
    wrong_length.send_head()
    wrong_length.send_bytes(data[:-1])
    status, body = wrong_length.response()
    assert status == 400, f"a declared length that does not match the registered object must be refused, got {status}"
    assert json.loads(body)["errors"][0]["pointer"] == "/headers/content-length"

    assert _leftover_tmp(server) == [], "a refused PUT must not leave temp files"
    answer = _commit(server, capture_id, path, data)
    assert answer["notFound"] == [path], "none of the refused PUTs may commit anything"


def test_eight_concurrent_puts_with_two_open_event_polls_all_finish(server: Server) -> None:
    capture_id = _create_capture(server, "tcp-burst-1")
    files = {f"photos/f{index}.jpg": _pattern(32 * 1024, seed=index) for index in range(8)}
    batch = [
        {
            "path": path,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "md5": _md5_b64(data),
            "role": "still",
            "contentType": "image/jpeg",
        }
        for path, data in files.items()
    ]
    registered = server.client.post(f"/v1/captures/{capture_id}/files", json={"files": batch})
    assert registered.status_code == 200, registered.text
    targets = {entry["path"]: entry["upload"] for entry in registered.json()["files"]}

    poll_results: dict[int, tuple[float, int]] = {}

    def poll(name: int) -> None:
        started = time.monotonic()
        answer = server.client.get(f"/v1/captures/{capture_id}/events", params={"after": 0, "wait": 10})
        poll_results[name] = (time.monotonic() - started, answer.status_code)

    polls = [threading.Thread(target=poll, args=(name,)) for name in range(2)]
    for thread in polls:
        thread.start()

    put_results: dict[str, tuple[float, int | None, str]] = {}
    lock = threading.Lock()

    def put(path: str) -> None:
        data = files[path]
        target = targets[path]
        started = time.monotonic()
        # The app's rules: a 429 waits Retry-After and retries; a dropped
        # connection retries the same URL. Both bounded.
        last_status: int | None = None
        for _attempt in range(6):
            try:
                answer = _put(server, target, data)
            except httpx.TransportError:
                time.sleep(0.2)
                continue
            if answer.status_code == 429:
                time.sleep(min(float(answer.headers.get("Retry-After", "1")), 2))
                continue
            last_status = answer.status_code
            break
        with lock:
            put_results[path] = (time.monotonic() - started, last_status, path)

    threads = [threading.Thread(target=put, args=(path,), name=path) for path in files]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)
        assert not thread.is_alive(), f"the PUT of {thread.name} never finished"
    for thread in polls:
        thread.join(timeout=25)
        assert not thread.is_alive(), "an event long-poll stalled past its bound"

    for path, (elapsed, status, _name) in put_results.items():
        assert status == 200, f"{path} never committed: last status {status}"
        assert elapsed < 25, f"{path} took {elapsed:.1f}s"
    for elapsed, status in poll_results.values():
        assert status == 200
        assert elapsed < 15, f"a long-poll took {elapsed:.1f}s, longer than its wait"

    commit_body = {
        "files": [{"path": path, "sha256": hashlib.sha256(data).hexdigest()} for path, data in files.items()]
    }
    committed = server.client.post(f"/v1/captures/{capture_id}/files:commit", json=commit_body)
    assert committed.status_code == 200, committed.text
    answer = committed.json()
    assert answer["committed"] == sorted(files), answer
    assert not answer["notFound"] and not answer["mismatch"]
    for path, data in files.items():
        assert _stored(server, capture_id, path) == data
    assert _leftover_tmp(server) == []


def test_a_storage_put_ignores_an_authorization_header(server: Server) -> None:
    """`ScopedCaptureHTTP.upload` refuses to send Authorization on a storage PUT,
    so the storage boundary must never require one. A header that leaks in
    anyway is ignored: it grants nothing and blocks nothing."""
    capture_id = _create_capture(server, "tcp-auth-1")
    data = _pattern(24 * 1024, seed=11)
    path = "photos/g.jpg"
    target = _register(server, capture_id, path, data)

    with_token = _put(server, target, data, **{"Authorization": f"Bearer {TOKEN}"})
    assert with_token.status_code == 200, with_token.text
    assert _commit(server, capture_id, path, data)["committed"] == [path]

    second = _register(server, capture_id, "photos/g2.jpg", data)
    with_garbage = _put(server, second, data, **{"Authorization": "Basic !!!not-a-credential"})
    assert with_garbage.status_code == 200, with_garbage.text
    assert _commit(server, capture_id, "photos/g2.jpg", data)["committed"] == ["photos/g2.jpg"]
    assert _stored(server, capture_id, path) == data
