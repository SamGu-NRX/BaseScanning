"""The FAKE_HUNTER plumbing fake: the published capture API, in this process.

Modeled on the public Processing/FixtureCaptureHTTP.swift: create (replayed
by packet id), register with PUT targets, PUT, commit as the only
acknowledgement, finalize with the packet, events, result, and an answer
per configuration. It is served over real loopback HTTP so the client's
real transport runs. Unlike the Swift fixture it checks digests at commit,
so the integrity controls have something to catch.

It is never evidence about any backend. Rows produced against it must be
labeled FAKE_HUNTER, and the knobs below exist to make it misbehave on
purpose so each client check can be shown to fail when it should.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ANSWERS = (
    "candidate",
    "needs_views",
    "manual_review",
    "not_eligible",
    "failed",
    "expired",
    "not_ready",
    "wrong_run",
    "hold",
)
_END_STATUS = {
    "candidate": "complete",
    "not_eligible": "complete",
    "not_ready": "complete",
    "wrong_run": "complete",
    "needs_views": "needs_views",
    "manual_review": "manual_review",
    "failed": "failed",
    "expired": "expired",
    "hold": "processing",
}
_KIND = {
    "candidate": "eligible",
    "needs_views": "needs_more_photos",
    "manual_review": "manual_review",
    "not_eligible": "not_eligible",
    "wrong_run": "eligible",
    "not_ready": "eligible",
}


@dataclasses.dataclass
class FakeConfig:
    answer: str = "candidate"
    sign_content_type: bool = True  # the Swift fixture signs Content-Type
    sign_authorization: bool = False  # a signed set that wrongly holds Authorization
    storage_base: str | None = None  # override the storage origin (e.g. insecure)
    register_order: str = "same"  # "same" | "reversed"
    register_drop_last: bool = False
    register_duplicate_first: bool = False
    register_extra_path: bool = False
    register_all_committed: bool = False
    commit_extra_path: bool = False  # report a path nobody committed
    commit_everything: bool = False  # ignore digests: commit whatever was uploaded
    verify_put_digest: bool = False  # storage refuses a PUT whose bytes miss the digest
    retry_finalize_once: bool = False
    result_run_id: str | None = None  # serve this runId on the result
    require_token: str | None = None
    canary: str | None = None  # plant this marker in every response
    forget_file: Path | None = None  # when it exists, drop all state (restart loss)


class FakeState:
    def __init__(self, config: FakeConfig) -> None:
        self.config = config
        self.lock = threading.Lock()
        self.captures: dict[str, dict] = {}
        self.by_packet: dict[str, str] = {}
        self.tokens: dict[str, tuple[str, str]] = {}
        self.next = 1
        self.put_headers: list[dict[str, str]] = []
        self.api_auth: list[str | None] = []

    def maybe_forget(self) -> None:
        marker = self.config.forget_file
        if marker is not None and marker.exists():
            marker.unlink()
            self.captures.clear()
            self.by_packet.clear()
            self.tokens.clear()


def _handler(state: FakeState, base_holder: dict):
    cfg = state.config

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        def _send(self, status: int, payload: dict | None) -> None:
            if payload is not None and cfg.canary:
                payload = {**payload, f"{cfg.canary}-key": cfg.canary}
            body = b"" if payload is None else json.dumps(payload, sort_keys=True).encode()
            self.send_response(status)
            if payload is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, code: str) -> None:
            item = {"code": code}
            if cfg.canary:
                item["message"] = f"internal detail {cfg.canary}"
            self._send(status, {"errors": [item]})

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _authorized(self) -> bool:
            auth = self.headers.get("Authorization")
            state.api_auth.append(auth)
            if cfg.require_token is None:
                return True
            return auth == f"Bearer {cfg.require_token}"

        def _route(self) -> tuple[list[str], dict]:
            parsed = urllib.parse.urlsplit(self.path)
            parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
            if parts and parts[0] == "v1":
                parts = parts[1:]
            return parts, dict(urllib.parse.parse_qsl(parsed.query))

        def do_GET(self) -> None:  # noqa: N802
            with state.lock:
                state.maybe_forget()
                parts, query = self._route()
                if parts == ["healthz"]:
                    self._send(200, {"status": "ok"})
                    return
                if not self._authorized():
                    self._error(401, "harness_schema")
                    return
                cap = (
                    state.captures.get(parts[1])
                    if len(parts) == 3 and parts[0] == "captures"
                    else None
                )
                if cap is None:
                    self._error(404, "capture_not_found")
                    return
                if parts[2] == "events":
                    self._events(cap, int(query.get("after", "0")))
                elif parts[2] == "result":
                    self._result(cap)
                else:
                    self._error(404, "harness_no_route")

        def do_POST(self) -> None:  # noqa: N802
            body = self._body()
            with state.lock:
                state.maybe_forget()
                parts, _ = self._route()
                if not self._authorized():
                    self._error(401, "harness_schema")
                    return
                if parts == ["captures"]:
                    self._create(body)
                    return
                cap = (
                    state.captures.get(parts[1])
                    if len(parts) == 3 and parts[0] == "captures"
                    else None
                )
                if cap is None:
                    self._error(404, "capture_not_found")
                    return
                if parts[2] == "files":
                    self._register(cap, body)
                elif parts[2] == "files:commit":
                    self._commit(cap, body)
                elif parts[2] == "finalize":
                    self._finalize(cap, body)
                else:
                    self._error(404, "harness_no_route")

        def do_PUT(self) -> None:  # noqa: N802
            body = self._body()
            with state.lock:
                parts, _ = self._route()
                state.put_headers.append({k.lower(): v for k, v in self.headers.items()})
                target = (
                    state.tokens.get(parts[1]) if len(parts) == 2 and parts[0] == "upload" else None
                )
                if target is None or self.headers.get("Authorization") is not None:
                    self._send(403, None)
                    return
                cap = state.captures.get(target[0])
                if cap is None:
                    self._send(403, None)
                    return
                expected = cap["registered"].get(target[1])
                if cfg.verify_put_digest and hashlib.sha256(body).hexdigest() != expected:
                    self._send(400, None)
                    return
                cap["stored"][target[1]] = body
                self._send(200, None)

        # -- API --------------------------------------------------------------

        def _create(self, body: bytes) -> None:
            try:
                request = json.loads(body)
                packet_id = request["packetId"]
                assert set(request) == {"packetId", "formatVersion", "tier", "flow", "device"}
            except (ValueError, KeyError, TypeError, AssertionError):
                self._error(422, "harness_schema")
                return
            if self.headers.get("Idempotency-Key") != packet_id:
                self._error(422, "harness_schema")
                return
            if packet_id in state.by_packet:
                cid, status = state.by_packet[packet_id], 200
            else:
                prefix = f"{cfg.canary}-cap" if cfg.canary else "cap_fake"
                cid = f"{prefix}_{state.next}"
                state.next += 1
                state.captures[cid] = {
                    "id": cid,
                    "packet": packet_id,
                    "registered": {},
                    "stored": {},
                    "committed": set(),
                    "listed": None,
                    "run": None,
                    "retried": False,
                }
                state.by_packet[packet_id] = cid
                status = 201
            self._send(
                status, {"captureId": cid, "status": "uploading", "upload": {"maxBatch": 50}}
            )

        def _register(self, cap: dict, body: bytes) -> None:
            try:
                files = json.loads(body)["files"]
                for f in files:
                    assert set(f) == {"path", "bytes", "sha256", "md5", "role", "contentType"}
            except (ValueError, KeyError, TypeError, AssertionError):
                self._error(422, "harness_schema")
                return
            base = cfg.storage_base or base_holder["origin"]
            entries = []
            for f in files:
                cap["registered"][f["path"]] = f["sha256"]
                if cfg.register_all_committed or f["path"] in cap["committed"]:
                    entries.append({"path": f["path"], "state": "committed"})
                    continue
                token = uuid.uuid4().hex
                state.tokens[token] = (cap["id"], f["path"])
                headers = {}
                if cfg.sign_content_type:
                    headers["Content-Type"] = f["contentType"]
                if cfg.sign_authorization:
                    headers["Authorization"] = "Bearer storage-signed"
                entries.append(
                    {
                        "path": f["path"],
                        "state": "pending",
                        "upload": {
                            "method": "PUT",
                            "url": f"{base}/upload/{token}",
                            "headers": headers,
                            "expiresAt": "2099-01-01T00:00:00Z",
                        },
                    }
                )
            if cfg.register_order == "reversed":
                entries.reverse()
            if cfg.register_drop_last and entries:
                entries.pop()
            if cfg.register_duplicate_first and entries:
                entries.append(dict(entries[0]))
            if cfg.register_extra_path:
                extra = f"{cfg.canary}-path" if cfg.canary else "synthetic/scan99.bin"
                entries.append(
                    {
                        "path": extra,
                        "state": "pending",
                        "upload": {"method": "PUT", "url": f"{base}/upload/none", "headers": {}},
                    }
                )
            self._send(200, {"files": entries})

        def _commit(self, cap: dict, body: bytes) -> None:
            try:
                files = json.loads(body)["files"]
                for f in files:
                    assert set(f) == {"path", "sha256"}
            except (ValueError, KeyError, TypeError, AssertionError):
                self._error(422, "harness_schema")
                return
            committed, not_found, mismatch = [], [], []
            for f in files:
                stored = cap["stored"].get(f["path"])
                if stored is None:
                    not_found.append(f["path"])
                elif cfg.commit_everything or (
                    hashlib.sha256(stored).hexdigest()
                    == f["sha256"]
                    == cap["registered"].get(f["path"])
                ):
                    committed.append(f["path"])
                    cap["committed"].add(f["path"])
                else:
                    mismatch.append(f["path"])
            if cfg.commit_extra_path:
                committed.append(f"{cfg.canary}-path" if cfg.canary else "synthetic/scan98.bin")
            self._send(200, {"committed": committed, "notFound": not_found, "mismatch": mismatch})

        def _finalize(self, cap: dict, body: bytes) -> None:
            try:
                packet = json.loads(body)
                listed = [f["path"] for f in packet["files"]]
            except (ValueError, KeyError, TypeError):
                self._error(422, "harness_schema")
                return
            if packet.get("packetId") != cap["packet"]:
                self._error(422, "harness_packet_id_mismatch")
                return
            if (
                self.headers.get("X-Packet-Sha256") != hashlib.sha256(body).hexdigest()
                or self.headers.get("Idempotency-Key") != cap["packet"]
            ):
                self._error(422, "harness_schema")
                return
            if cap["run"] is None:
                cap["listed"] = listed
                prefix = f"{cfg.canary}-run" if cfg.canary else "run_fake"
                cap["run"] = f"{prefix}_{state.next}"
                state.next += 1
            missing = [p for p in cap["listed"] if p not in cap["committed"]]
            self._send(
                202,
                {
                    "status": "awaiting_files" if missing else "processing",
                    "missing": missing,
                    "runId": cap["run"],
                },
            )

        def _all_committed(self, cap: dict) -> bool:
            return cap["listed"] is not None and all(p in cap["committed"] for p in cap["listed"])

        def _events(self, cap: dict, after: int) -> None:
            if cap["run"] is None or not self._all_committed(cap) or cfg.answer == "hold":
                status = "processing" if self._all_committed(cap) else "awaiting_files"
                if cap["run"] is None:
                    status = "uploading"
                self._send(200, {"status": status, "next": after, "events": []})
                return
            if cfg.retry_finalize_once and not cap["retried"]:
                cap["retried"] = True
                cap["run"] = None  # the server lost the finalize
                event = {
                    "seq": after + 1,
                    "type": "failed",
                    "at": "2026-01-01T00:00:00Z",
                    "data": {"next": "retry_finalize", "code": "harness_fault"},
                }
                self._send(200, {"status": "processing", "next": after + 1, "events": [event]})
                return
            end = _END_STATUS[cfg.answer]
            ended = end in {"failed", "expired"}
            data = {"runId": cap["run"]}
            if cfg.canary:
                data["stage"] = cfg.canary
            event = {
                "seq": after + 1,
                "type": "failed" if ended else "verdict_ready",
                "at": "2026-01-01T00:00:00Z",
                "data": data,
            }
            if cfg.canary:
                event["type"] = cfg.canary if not ended else event["type"]
            self._send(200, {"status": end, "next": after + 1, "events": [event]})

        def _result(self, cap: dict) -> None:
            if cap["run"] is None or not self._all_committed(cap) or cfg.answer == "hold":
                self._send(
                    200,
                    {
                        "runId": cap["run"],
                        "status": "processing",
                        "viewsNeeded": [],
                        "memberActions": [],
                        "outcome": None,
                    },
                )
                return
            if cfg.answer == "not_ready":
                self._error(404, "result_not_ready")
                return
            end = _END_STATUS[cfg.answer]
            outcome = None
            if cfg.answer in _KIND:
                outcome = {
                    "kind": _KIND[cfg.answer],
                    "profile": "fake",
                    "message": cfg.canary or "Fake answer: says nothing about any wall.",
                    "viewsNeeded": [],
                    "reasons": [],
                }
            views = []
            if cfg.answer == "needs_views":
                views = [
                    {
                        "id": "view_fake_1",
                        "kind": "wall_band",
                        "criteria": [],
                        "why": "unseen",
                        "promptId": "prompt_fake_1",
                        "prompt": {"title": "Fake: one more view", "body": "Fake prompt."},
                    }
                ]
            run = cap["run"]
            if cfg.answer == "wrong_run":
                run = "run_fake_other"
            if cfg.result_run_id is not None:
                run = cfg.result_run_id
            payload = {
                "runId": run,
                "status": end,
                "viewsNeeded": views,
                "memberActions": [],
                "outcome": outcome,
            }
            if cfg.canary:
                payload["previewUrl"] = f"https://storage.invalid/p.png?X-Signature={cfg.canary}"
            self._send(200, payload)

    return Handler


class FakeApi:
    """A running fake: ``base_url`` is the API base, ``state`` its memory."""

    def __init__(self, config: FakeConfig | None = None) -> None:
        self.state = FakeState(config or FakeConfig())
        holder: dict = {}
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self.state, holder))
        self._server.daemon_threads = True
        host, port = self._server.server_address[:2]
        holder["origin"] = f"http://{host}:{port}"
        self.origin = holder["origin"]
        self.base_url = f"{self.origin}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> FakeApi:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
