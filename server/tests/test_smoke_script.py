"""examples/smoke.py sends the private key only over HTTPS (or to this machine), and never
follows a redirect with it."""

import http.server
import importlib.util
import threading
import urllib.request
from pathlib import Path
from typing import ClassVar

import pytest

SMOKE = Path(__file__).resolve().parents[1] / "examples" / "smoke.py"
spec = importlib.util.spec_from_file_location("smoke", SMOKE)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


@pytest.mark.parametrize(
    "url",
    [
        "http://house-scanning-server-private.vercel.app",
        "http://192.168.1.20:8000",
        "ftp://x.example",
    ],
)
def test_a_key_is_never_sent_in_the_clear(url: str) -> None:
    with pytest.raises(SystemExit, match="https"):
        smoke.check_keyed_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://house-scanning-server-private.vercel.app",
        "http://localhost:8000",
        "http://127.0.0.1:9",
    ],
)
def test_https_and_this_machine_may_carry_a_key(url: str) -> None:
    smoke.check_keyed_url(url)


class Recorder(http.server.BaseHTTPRequestHandler):
    """Answers every POST; `target` redirects to it, and records what reached it."""

    seen: ClassVar[list[str | None]] = []
    location = ""

    def do_POST(self) -> None:
        if self.location:
            self.send_response(302)
            self.send_header("Location", self.location)
            self.end_headers()
            return
        type(self).seen.append(self.headers.get("Authorization"))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    # urllib follows a 302 as a GET, carrying the request's headers with it.
    do_GET = do_POST

    def log_message(self, *args: object) -> None:
        pass


def serve(handler: type) -> http.server.HTTPServer:
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_a_keyed_request_does_not_follow_a_redirect() -> None:
    target = serve(type("Target", (Recorder,), {"seen": [], "location": ""}))
    port = target.server_address[1]
    redirecting = serve(
        type("Redirect", (Recorder,), {"seen": [], "location": f"http://127.0.0.1:{port}/"})
    )
    try:
        url = f"http://127.0.0.1:{redirecting.server_address[1]}/v1/placements"
        status, body = smoke.post(url, b"{}", "secret-for-this-test")
        assert status == 302
        assert body["error"]["code"] == "redirect_refused"
        assert target.RequestHandlerClass.seen == []  # the key never reached the other server
    finally:
        target.shutdown()
        redirecting.shutdown()


def test_a_keyed_request_never_goes_through_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    # CodeRabbit on 9edcd4b: with http_proxy set, a keyed request to localhost could go through
    # the proxy, key included. Load the script with a proxy configured: the keyed opener must
    # still have no proxy handler (an empty proxy table registers none).
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    assert not [h for h in fresh._KEYED.handlers if isinstance(h, urllib.request.ProxyHandler)]
