"""Labeled FAKE_HUNTER fault controls.

A local reverse proxy in front of the real endpoint. Faults inject only
conditions the real local service cannot deterministically produce on
demand: latency, canned error statuses, truncated bodies and truncated
response framing, connection aborts, expired signed upload URLs, a canned
unknown-capture answer, and a retry_finalize event stream. They exist to
prove the client harness reacts to the condition at all — a FAKE_HUNTER
check can never substitute for a passing real check and never grounds a
backend-behavior claim (see modes.Transport).

Faults are keyed by step (``step_for`` maps a request to one), and each
fault accepts two optional gates:

  after          apply the fault only from this 0-based hit of the step on
  recover_after  stop applying it after this many applied injections

so a round can prove both detection (fault applied → harness reports it)
and recovery (fault stops → a client that fixed the condition completes).

Synthetic error bodies use only ``harness_*`` codes so they can never be
mistaken for published contract codes. The one deliberate exception is
``capture_not_found``: that fault exists precisely to emulate the
published unknown-capture answer (its recovery contract keys on that
exact code), and the FAKE_HUNTER transport label on every receipt row it
touches keeps it from ever reading as a real-backend observation.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Fixed synthetic timestamp for events the proxy itself fabricates. It
# must never look like a backend-produced observation time.
_INJECTED_EVENT_AT = "2026-01-01T00:00:00Z"


def step_for(method: str, path: str) -> str:
    """Map a proxied request to the interface step it belongs to."""
    clean = path.split("?", 1)[0]
    tail = clean.rstrip("/").rsplit("/", 1)[-1]
    if method == "PUT":
        return "upload"
    if tail == "healthz":
        return "health"
    if clean.endswith("/captures") and method == "POST":
        return "create"
    if tail == "files" and method == "POST":
        return "register"
    if tail.endswith(":commit"):
        return "commit"
    if tail == "finalize":
        return "finalize"
    if tail == "events":
        return "events"
    if tail == "result":
        return "result"
    return "unknown"


def _canned_status(kind: str, fault: dict) -> tuple[int, str]:
    """Status + error code for the request-replacing fault kinds."""
    if kind == "status":
        return int(fault.get("code", 503)), str(fault.get("code_str", "harness_fault"))
    if kind == "expired_url":
        # Emulates an expired signed upload URL: the published client treats
        # a 403 on the storage PUT as "re-register for a fresh URL".
        return 403, "harness_url_expired"
    if kind == "capture_not_found":
        # Deliberate published-code fault (see module docstring).
        return 404, "capture_not_found"
    raise ValueError(f"not a canned-response fault kind: {kind!r}")


def _truncate_response_payload(payload: bytes, fault: dict) -> tuple[bytes, int]:
    """Fewer body bytes than the declared Content-Length, then close.

    The declared length stays plausible (the body's own length plus the
    withheld tail); the socket closes short of it, so a reading client
    must surface the framing mismatch rather than parse a short body.
    """
    withhold = max(1, int(fault.get("bytes", 16)))
    written = payload[: max(0, len(payload) - withhold)] if payload else b""
    declared = len(written) + withhold
    return written, declared


def _inject_retry_finalize(payload: bytes) -> bytes | None:
    """Append a ``failed`` event carrying ``data.next == "retry_finalize"``.

    Returns the mutated body, or None when the upstream answer is not a
    well-formed events response (the fault then does not apply — a
    response the published client cannot parse cannot carry the signal).
    """
    try:
        answer = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not (isinstance(answer, dict) and isinstance(answer.get("events"), list)):
        return None
    seqs = [
        e.get("seq")
        for e in answer["events"]
        if isinstance(e, dict) and isinstance(e.get("seq"), int)
    ]
    try:
        cursor = int(answer.get("next", 0) or 0)
    except (TypeError, ValueError):
        cursor = 0
    injected_seq = max(seqs, default=cursor) + 1
    answer["events"].append(
        {
            "seq": injected_seq,
            "type": "failed",
            "at": _INJECTED_EVENT_AT,
            "data": {"next": "retry_finalize"},
        }
    )
    answer["next"] = max(cursor, injected_seq)
    return json.dumps(answer).encode("utf-8")


class FaultProxy:
    """Threading reverse proxy with per-step fault injection."""

    def __init__(self, origin_url: str, faults: dict[str, dict]) -> None:
        parsed = urllib.parse.urlparse(origin_url)
        self._origin_host = parsed.hostname or "127.0.0.1"
        self._origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self._origin_prefix = parsed.path.rstrip("/")
        self._faults = dict(faults)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None
        self.hit_counts: dict[str, int] = {}
        # Times each fault was actually APPLIED to a request/response —
        # the anti-vacuity counter: a round that claims detection of a
        # fault that never fired is itself a failed round.
        self.injected_counts: dict[str, int] = {}

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}{self._origin_prefix}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        if self._thread:
            self._thread.join(timeout=5)

    # -- fault gating --------------------------------------------------------

    def _active_fault(self, step: str) -> dict | None:
        """The fault configured for this step, if it applies to this hit."""
        fault = self._faults.get(step)
        if not fault:
            return None
        hit_index = self.hit_counts.get(step, 0) - 1  # hit_counts already bumped
        after = int(fault.get("after", 0))
        if hit_index < after:
            return None
        injected = hit_index - after
        recover_after = fault.get("recover_after")
        if recover_after is not None and injected >= int(recover_after):
            return None
        return fault

    def _note_injected(self, step: str) -> None:
        self.injected_counts[step] = self.injected_counts.get(step, 0) + 1

    # -- request handling ---------------------------------------------------

    def _handler(self):  # noqa: N802 - BaseHTTPRequestHandler style
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # silence default stderr chatter
                pass

            def _read_body(self) -> bytes:
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _respond(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _forward(self, method: str) -> None:
                path = self.path
                step = step_for(method, path)
                proxy.hit_counts[step] = proxy.hit_counts.get(step, 0) + 1
                fault = proxy._active_fault(step)
                body = self._read_body()
                if fault:
                    kind = fault.get("kind")
                    if kind == "delay":
                        time.sleep(fault.get("ms", 0) / 1000.0)
                        proxy._note_injected(step)
                    elif kind in ("status", "expired_url", "capture_not_found"):
                        code, code_str = _canned_status(kind, fault)
                        # Record before the response goes out: by the time any
                        # client can observe this fault, the counter must show it.
                        proxy._note_injected(step)
                        self._respond(code, {"errors": [{"code": code_str}]})
                        return
                    elif kind == "abort":
                        proxy._note_injected(step)
                        self.close_connection = True
                        return
                    if kind == "truncate":
                        body = body[: max(0, len(body) - fault.get("bytes", 1))]
                        proxy._note_injected(step)
                forward_headers = {
                    k: v
                    for k, v in self.headers.items()
                    if not k.lower().startswith(("connection", "keep-alive", "proxy-"))
                }
                conn = http.client.HTTPConnection(
                    proxy._origin_host, proxy._origin_port, timeout=60
                )
                try:
                    conn.request(method, path, body=body or None, headers=forward_headers)
                    upstream = conn.getresponse()
                    payload = upstream.read()
                    self.send_response(upstream.status)
                    for k, v in upstream.getheaders():
                        lowered = k.lower()
                        if lowered.startswith(("transfer-encoding", "connection", "keep-alive")):
                            continue
                        if lowered == "content-length":
                            continue  # re-sent below with the actual payload length
                        self.send_header(k, v)
                    # response-side faults
                    if fault and fault.get("kind") == "truncate_response":
                        written, declared = _truncate_response_payload(payload, fault)
                        proxy._note_injected(step)
                        self.send_header("Content-Length", str(declared))
                        self.end_headers()
                        self.wfile.write(written)
                        self.close_connection = True
                        return
                    if fault and fault.get("kind") == "retry_finalize" and upstream.status == 200:
                        mutated = _inject_retry_finalize(payload)
                        if mutated is not None:
                            payload = mutated
                            proxy._note_injected(step)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (OSError, http.client.HTTPException):
                    self._respond(
                        502,
                        {"errors": [{"code": "harness_origin_unreachable"}]},
                    )
                finally:
                    conn.close()

            def do_GET(self) -> None:  # noqa: N802
                self._forward("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._forward("POST")

            def do_PUT(self) -> None:  # noqa: N802
                self._forward("PUT")

        return Handler


def serve(origin_url: str, faults: dict[str, dict]) -> FaultProxy:
    """Build, bind and start a fault proxy."""
    proxy = FaultProxy(origin_url, faults)
    proxy.start()
    return proxy


def wait_tcp(url: str, timeout_s: float = 10.0) -> bool:
    """Block until the endpoint accepts a TCP connection."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False
