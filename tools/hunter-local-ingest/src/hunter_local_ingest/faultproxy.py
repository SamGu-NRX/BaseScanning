"""Labeled FAKE_HUNTER fault controls.

A local reverse proxy in front of the real endpoint. Faults inject only
conditions the real local service cannot deterministically produce on
demand: latency, canned error statuses, truncated bodies, connection
aborts. They exist to prove the client harness reacts to the condition at
all — a FAKE_HUNTER check can never substitute for a passing real check
and never grounds a backend-behavior claim (see modes.Transport).

Synthetic error bodies use only ``harness_*`` codes so they can never be
mistaken for published contract codes.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


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
                fault = proxy._faults.get(step)
                body = self._read_body()
                if fault and fault.get("kind") == "delay":
                    time.sleep(fault.get("ms", 0) / 1000.0)
                if fault and fault.get("kind") == "status":
                    self._respond(
                        fault.get("code", 503),
                        {"errors": [{"code": fault.get("code_str", "harness_fault")}]},
                    )
                    return
                if fault and fault.get("kind") == "abort":
                    self.close_connection = True
                    return
                if fault and fault.get("kind") == "truncate":
                    body = body[: max(0, len(body) - fault.get("bytes", 1))]
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

    # expose the wrapped handler class for tests
    @property
    def _handler_class(self):  # pragma: no cover - introspection helper
        return self._handler()


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
