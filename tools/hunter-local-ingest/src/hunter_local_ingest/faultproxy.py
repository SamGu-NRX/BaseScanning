"""Labeled FAKE_HUNTER fault injection: a local reverse proxy.

The proxy sits in front of the endpoint under test and injects conditions
the real service cannot produce on demand: latency, a canned status, a
connection closed without an answer, a request body cut short, or a
response body shorter than its declared length. A FAKE_HUNTER check shows
the client reacts to the condition; it never stands in for a real check.

Synthetic error bodies use ``harness_*`` codes only, so they can never be
mistaken for published contract codes.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .netpolicy import parse_api_base

FAULT_KINDS = frozenset({"delay", "status", "abort", "truncate_request", "truncate_response"})
_HOP_BY_HOP = frozenset(
    {"connection", "keep-alive", "te", "trailer", "transfer-encoding", "upgrade"}
)


def _hop_by_hop(name: str) -> bool:
    lowered = name.lower()
    return lowered in _HOP_BY_HOP or lowered.startswith("proxy-")


def step_for(method: str, path: str) -> str:
    """Map a proxied request to the interface step it belongs to."""
    clean = path.split("?", 1)[0].rstrip("/")
    tail = clean.rsplit("/", 1)[-1]
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
    if tail in {"finalize", "events", "result"}:
        return tail
    return "unknown"


class FaultProxy:
    """Threading reverse proxy with per-step faults.

    ``faults`` maps a step to ``{"kind": ..., "times": n}`` plus the kind's
    parameters (``ms`` for delay; ``code`` and ``retry_after`` for status;
    ``bytes`` for the truncations). ``times`` bounds how many requests the
    fault hits; omitted means every request.
    """

    def __init__(self, origin_url: str, faults: dict[str, dict]) -> None:
        origin = parse_api_base(origin_url)
        for spec in faults.values():
            if spec.get("kind") not in FAULT_KINDS:
                raise ValueError("unknown fault kind")
        self._origin = origin
        self._faults = {step: dict(spec) for step, spec in faults.items()}
        self._lock = threading.Lock()
        self.hit_counts: dict[str, int] = {}
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}{self._origin.base_path}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def origin_connection(self, timeout_s: float = 60.0) -> http.client.HTTPConnection:
        """An https origin gets TLS; plain http is only ever loopback (netpolicy)."""
        origin = self._origin
        if origin.scheme == "https":
            return http.client.HTTPSConnection(origin.host, origin.port, timeout=timeout_s)
        return http.client.HTTPConnection(origin.host, origin.port, timeout=timeout_s)

    def _take(self, step: str) -> dict | None:
        """Count the hit and return the fault to apply to it, if any."""
        with self._lock:
            self.hit_counts[step] = self.hit_counts.get(step, 0) + 1
            spec = self._faults.get(step)
            if spec is None:
                return None
            times = spec.get("times")
            if times is not None:
                if times <= 0:
                    return None
                spec["times"] = times - 1
            return dict(spec)

    def _handler(self):
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:
                pass

            def _respond(self, status: int, payload: dict, extra: dict | None = None) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for name, value in (extra or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def _forward(self, method: str) -> None:
                step = step_for(method, self.path)
                fault = proxy._take(step)
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                kind = fault.get("kind") if fault else None
                if kind == "delay":
                    time.sleep(fault.get("ms", 0) / 1000.0)
                elif kind == "status":
                    extra = {}
                    if fault.get("retry_after") is not None:
                        extra["Retry-After"] = str(fault["retry_after"])
                    self._respond(
                        fault.get("code", 503), {"errors": [{"code": "harness_fault"}]}, extra
                    )
                    return
                elif kind == "abort":
                    # Close without a status line: the client sees no answer.
                    self.close_connection = True
                    return
                elif kind == "truncate_request":
                    body = body[: max(0, len(body) - fault.get("bytes", 1))]
                headers = {
                    k: v
                    for k, v in self.headers.items()
                    if not _hop_by_hop(k) and k.lower() not in {"host", "content-length"}
                }
                origin = proxy._origin
                headers["Host"] = f"{origin.host}:{origin.port}"
                if body or method in {"POST", "PUT"}:
                    # The length always matches the bytes actually forwarded.
                    headers["Content-Length"] = str(len(body))
                conn = proxy.origin_connection()
                try:
                    conn.request(method, self.path, body=body if body else None, headers=headers)
                    upstream = conn.getresponse()
                    payload = upstream.read()
                    status = upstream.status
                    passthrough = [
                        (k, v)
                        for k, v in upstream.getheaders()
                        if not _hop_by_hop(k) and k.lower() != "content-length"
                    ]
                except (OSError, http.client.HTTPException):
                    self._respond(502, {"errors": [{"code": "harness_origin_unreachable"}]})
                    return
                finally:
                    conn.close()
                self.send_response(status)
                for k, v in passthrough:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if kind == "truncate_response":
                    # Declare the full length, send less, then close: the
                    # client must see a framing error, not a short body.
                    self.wfile.write(payload[: max(0, len(payload) - fault.get("bytes", 1))])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self.wfile.write(payload)

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
    """Block until the API base accepts a TCP connection."""
    base = parse_api_base(url)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((base.host, base.port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False
