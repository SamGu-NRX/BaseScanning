"""Replay client against a fake capture API, and nested fixture tars."""

from __future__ import annotations

import io
import json
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from server.contracts.api import CommitRequest
from server.eval.replay import main, plan_uploads, replay


def _packet(tmp: Path) -> Path:
    source = json.loads(Path("data/fixtures/packets/examples/example-packet.json").read_text())
    source["packetId"] = "pkt-replay-client"
    folder = tmp / "packet"
    folder.mkdir()
    (folder / "packet.arkit-lidar.json").write_text(json.dumps(source))
    needed = [item.path for item in plan_uploads(source, variant="arkit-lidar").files]
    for rel in needed:
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"bytes-for-" + rel.encode())
    return folder


class _API:
    def __init__(self) -> None:
        self.captures: dict[str, str] = {}
        self.puts: list[str] = []
        self.commits: list[list[str]] = []
        self.finalizes = 0
        self.events_polled = 0
        self.multipart = 0
        self.mismatch_once = True
        self.fail_put_once = False
        self._seq = 0

    def handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:
                del fmt, args

            def _read(self) -> bytes:
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _json(self, code: int, payload: dict) -> None:
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                body = self._read()
                host = f"http://{self.headers['Host']}"
                if parsed.path == "/v1/captures":
                    key = self.headers.get("Idempotency-Key") or "none"
                    if key not in api.captures:
                        api.captures[key] = f"cap-{len(api.captures) + 1}"
                        self._json(201, {"captureId": api.captures[key]})
                    else:
                        self._json(200, {"captureId": api.captures[key]})
                    return
                if parsed.path.endswith("/files"):
                    document = json.loads(body)
                    files = []
                    for row in document["files"]:
                        url = f"{host}/blob/{row['path']}"
                        if row["path"].endswith(".csv.gz"):
                            api.multipart += 1
                            files.append(
                                {
                                    "path": row["path"],
                                    "upload": {
                                        "method": "MULTIPART",
                                        "partBytes": 4,
                                        "parts": [
                                            {"url": url + "?part=0", "headers": {}},
                                            {"url": url + "?part=1", "headers": {}},
                                        ],
                                        "completeUrl": f"{host}/complete/{row['path']}",
                                    },
                                }
                            )
                        else:
                            files.append({"path": row["path"], "upload": {"method": "PUT", "url": url, "headers": {}}})
                    self._json(200, {"files": files})
                    return
                if parsed.path.endswith("/files:commit"):
                    committed_request = CommitRequest.model_validate_json(body)
                    paths = [item.path for item in committed_request.files]
                    api.commits.append(paths)
                    mismatch = []
                    if api.mismatch_once and paths and paths[0].endswith(".jpg"):
                        mismatch = [paths[0]]
                        api.mismatch_once = False
                    committed = [path for path in paths if path not in mismatch]
                    self._json(200, {"committed": committed, "mismatch": mismatch})
                    return
                if parsed.path.endswith("/finalize"):
                    api.finalizes += 1
                    if api.finalizes == 1 and self.headers.get("X-Early") == "1":
                        self._json(202, {"runId": "run-1", "status": "awaiting_files", "missing": ["rest"]})
                        return
                    missing = ["rest"] if api.finalizes == 1 and api.puts and len(api.commits) < 2 else []
                    status = "awaiting_files" if missing else "processing"
                    self._json(202, {"runId": "run-1", "status": status, "missing": missing})
                    return
                if parsed.path.startswith("/complete/"):
                    self._json(200, {"ok": True})
                    return
                self._json(404, {})

            def do_PUT(self) -> None:  # noqa: N802
                self._read()
                if api.fail_put_once:
                    api.fail_put_once = False
                    raw = b"unavailable"
                    self.send_response(503)
                    self.send_header("Retry-After", "0")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                api.puts.append(urlparse(self.path).path)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path in {f"/v1/captures/{capture}" for capture in api.captures.values()}:
                    count = len({path for batch in api.commits for path in batch})
                    self._json(200, {"status": "processing", "filesRegistered": count, "filesCommitted": count})
                    return
                if parsed.path.endswith("/events"):
                    api.events_polled += 1
                    after = int(parse_qs(parsed.query).get("after", ["0"])[0])
                    events = []
                    if after < 1:
                        events.append({"seq": 1, "type": "capture_check"})
                    if after < 2:
                        events.append({"seq": 2, "type": "verdict_ready"})
                    self._json(200, {"events": events})
                    return
                if parsed.path.endswith("/result"):
                    host = f"http://{self.headers['Host']}"
                    self._json(
                        200,
                        {
                            "runId": "run-1",
                            "status": "manual_review",
                            "viewsNeeded": [],
                            "memberActions": [],
                            "outcome": None,
                            "verdictUrl": f"{host}/artifact/verdict.json",
                        },
                    )
                    return
                if parsed.path.startswith("/artifact/"):
                    raw = b'{"evaluator": {"rules_profile": "C"}, "ok": true}'
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                self._json(404, {})

        return Handler


def _serve(api: _API):
    server = ThreadingHTTPServer(("127.0.0.1", 0), api.handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_replay_puts_commits_polls_and_is_idempotent(tmp_path: Path):
    folder = _packet(tmp_path)
    api = _API()
    server, base = _serve(api)
    try:
        first = replay(
            folder,
            base,
            "token",
            variant="arkit-lidar",
            rate=0,
            concurrency=2,
            finalize_at="last",
            until="verdict",
            out_dir=tmp_path / "out",
            sleeper=lambda _seconds: None,
        )
        second = replay(
            folder,
            base,
            "token",
            variant="arkit-lidar",
            rate=0,
            finalize_at="last",
            until="verdict",
            sleeper=lambda _seconds: None,
        )
    finally:
        server.shutdown()
    assert first.captureId == second.captureId == "cap-1"
    assert first.runId == second.runId == "run-1"
    assert api.puts
    assert api.commits
    assert any(path.startswith("/blob/") for path in api.puts)
    assert api.multipart >= 1
    assert api.events_polled >= 1
    assert first.events[-1]["type"] == "verdict_ready"
    assert "last_commit_to_capture_check_s" in first.timings
    assert first.result["verdictUrl"]
    downloaded = Path(first.verdict_paths["C"])
    assert downloaded == tmp_path / "out" / "verdict.json"
    assert json.loads(downloaded.read_text())["ok"] is True
    assert any("mismatch:" in err for err in first.errors) or api.mismatch_once is False


def test_early_finalize_then_continues(tmp_path: Path):
    folder = _packet(tmp_path)
    api = _API()
    api.mismatch_once = False
    server, base = _serve(api)
    try:
        run = replay(
            folder,
            base,
            "token",
            variant="arkit-lidar",
            rate=0,
            finalize_at="early",
            until="processing",
            sleeper=lambda _seconds: None,
        )
    finally:
        server.shutdown()
    assert run.captureId == "cap-1"
    assert api.finalizes >= 2
    assert not any("early finalize" in err for err in run.errors)


def test_put_retries_a_503(tmp_path: Path):
    folder = _packet(tmp_path)
    api = _API()
    api.fail_put_once = True
    api.mismatch_once = False
    server, base = _serve(api)
    slept: list[float] = []
    try:
        replay(
            folder,
            base,
            "token",
            variant="arkit-lidar",
            rate=0,
            finalize_at="last",
            until="processing",
            sleeper=slept.append,
        )
    finally:
        server.shutdown()
    assert slept  # the 503 honoured Retry-After via the sleeper


def test_nested_site_tar_dry_run(tmp_path: Path, capsys):
    source = Path("data/fixtures/packets/eth3d-facade/packet.arkit-lidar.json").read_bytes()
    archive = tmp_path / "eth3d-facade.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("eth3d-facade/packet.arkit-lidar.json")
        info.size = len(source)
        tar.addfile(info, fileobj=io.BytesIO(source))
    assert main(["--packet", str(archive), "--variant", "arkit-lidar", "--dry-run"]) == 0
    assert "packetId" in capsys.readouterr().out


def test_unreachable_api_with_a_short_timeout_exits_2(tmp_path: Path, capsys):
    """A deadline that expires before any response is exit 2, not the verdict-wait exit 5."""
    folder = _packet(tmp_path)
    code = main(
        [
            "--packet",
            str(folder),
            "--variant",
            "arkit-lidar",
            "--api",
            "http://127.0.0.1:9",
            "--timeout",
            "0.4",
            "--rate",
            "0",
        ]
    )
    assert code == 2
    assert "unreachable" in capsys.readouterr().err
