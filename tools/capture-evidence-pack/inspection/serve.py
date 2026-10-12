#!/usr/bin/env python3
"""Localhost inspection server for capture evidence packs.

Every request for evidence runs the independent reader (read.verify_pack) on
the pack and its referenced inputs, and the page renders only what that call
returned. A pack the reader refuses is served as a refusal, never as
evidence. The server does not open the archive itself. Binds 127.0.0.1 only
and makes no external network calls.

The fault and delay flags reproduce fetch states deterministically when
capturing evidence for the page's failure handling; defaults change nothing
about what a normal run serves. --pack serves a different pack through the
same verification, which is how a refused pack is shown.

Usage:
    python tools/capture-evidence-pack/inspection/serve.py [--port 8793]
        [--pack PATH]
        [--fault none|data|refusals|both|data-malformed|refusals-malformed|data-incomplete|refusals-incomplete]
        [--delay-data SECONDS] [--delay-refusals SECONDS]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shlex
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

INSPECTION_DIR = Path(__file__).resolve().parent
TOOL_DIR = INSPECTION_DIR.parent
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

import read  # noqa: E402  (the independent reader; never pack.py or validate.py)

EXAMPLE_PACK = TOOL_DIR / "evidence/example.zip"

# Capture roots that do not match the example pack. The server asks the
# reader to verify the example against each one on every request, and shows
# the outcome, so the page reports what the reader does now rather than what
# a receipt file says it once did.
REFUSAL_DEMOS = (
    ("missing-source", TOOL_DIR / "fixtures/missing-source"),
    ("mismatched-result", TOOL_DIR / "fixtures/mismatched-result"),
)

FAULTS = ("both", "data", "data-incomplete", "data-malformed", "none", "refusals", "refusals-incomplete", "refusals-malformed")

OPTIONS = {"fault": "none", "delay_data": 0.0, "delay_refusals": 0.0, "pack": EXAMPLE_PACK}

# A body that is not JSON, for the malformed-response faults.
MALFORMED_BODY = b'{"status": "verified", "evidence": '


def incomplete(payload: dict, read: str) -> dict:
    """A real payload with one record emptied, for the incomplete-response
    faults: valid JSON that the page must still refuse to show as evidence."""
    if read == "data" and payload.get("status") == "verified":
        payload["evidence"]["packaged_files"] = [{}]
    elif read == "refusals":
        payload["refusals"][0]["kind"] = None
    return payload


def read_command(pack: Path, capture_root: Path | None = None) -> str:
    parts = ["python", "tools/capture-evidence-pack/read.py", read.display_path(pack)]
    if capture_root is not None:
        parts += ["--capture-root", read.display_path(capture_root)]
    return shlex.join(parts)


def _verify(pack: Path, capture_root: Path | None) -> tuple[dict | None, read.Refusal | None, bytes | None]:
    pack_bytes = None
    try:
        pack_bytes = read.read_pack_file(pack, "the pack")
        return read.verify_pack(pack_bytes, capture_root), None, pack_bytes
    except read.Refusal as refusal:
        return None, refusal, pack_bytes


def verification_payload(pack: Path) -> dict:
    """What the page may show about the pack: the reader's verified values, or
    the reader's refusal and nothing else."""
    verified, refusal, pack_bytes = _verify(pack, None)
    base = {"pack_path": read.display_path(pack), "checked_by": "tools/capture-evidence-pack/read.py verify_pack", "command": read_command(pack)}
    if refusal is not None:
        return {
            **base,
            "status": "refused",
            "refusal": {
                "kind": refusal.kind,
                "detail": refusal.detail,
                "rejected_pack_sha256": read.sha256_hex(pack_bytes) if pack_bytes is not None else None,
            },
        }
    return {**base, "status": "verified", "evidence": verified}


def refusal_demo_payload() -> dict:
    demos = []
    for expected, root in REFUSAL_DEMOS:
        _, refusal, pack_bytes = _verify(EXAMPLE_PACK, root)
        if refusal is None:
            outcome = "not-refused"
        elif refusal.kind == expected:
            outcome = "refused"
        else:
            outcome = "refused-other-kind"
        demos.append(
            {
                "expected_kind": expected,
                "outcome": outcome,
                "kind": refusal.kind if refusal else None,
                "detail": refusal.detail if refusal else None,
                "rejected_pack_sha256": read.sha256_hex(pack_bytes) if refusal and pack_bytes is not None else None,
                "command": read_command(EXAMPLE_PACK, root),
            }
        )
    return {"refusals": demos}


class InspectionHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # http.server calls do_<METHOD>
        # A failure the reader did not turn into a refusal is a bug. Answer
        # 500 so the page shows a failed read instead of a dropped
        # connection, and log only the exception type: its text can carry
        # absolute paths.
        try:
            self.route()
        except Exception as error:
            print(f"inspection server: request failed with {type(error).__name__}", file=sys.stderr, flush=True)
            self.send_error(500)

    def route(self) -> None:
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        fault = OPTIONS["fault"]
        if path == "/":
            self.send_file(INSPECTION_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/data.json":
            time.sleep(OPTIONS["delay_data"])
            if fault in ("data", "both"):
                self.send_error(500)
            elif fault == "data-malformed":
                self.send_body(MALFORMED_BODY, "application/json")
            elif fault == "data-incomplete":
                self.send_json(incomplete(verification_payload(OPTIONS["pack"]), "data"))
            else:
                self.send_json(verification_payload(OPTIONS["pack"]))
        elif path == "/refusals.json":
            time.sleep(OPTIONS["delay_refusals"])
            if fault in ("refusals", "both"):
                self.send_error(500)
            elif fault == "refusals-malformed":
                self.send_body(MALFORMED_BODY, "application/json")
            elif fault == "refusals-incomplete":
                self.send_json(incomplete(refusal_demo_payload(), "refusals"))
            else:
                self.send_json(refusal_demo_payload())
        elif path == "/vendor/axe.min.js":
            self.send_file(INSPECTION_DIR / "vendor/axe.min.js", "application/javascript")
        else:
            self.send_error(404)

    def send_file(self, path: Path, mime: str) -> None:
        self.send_body(path.read_bytes(), mime)

    def send_json(self, payload: dict) -> None:
        self.send_body(json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"), "application/json")

    def send_body(self, body: bytes, mime: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8793)
    parser.add_argument("--pack", type=Path, default=EXAMPLE_PACK, help="pack to verify and show (default: the committed example)")
    parser.add_argument("--fault", choices=list(FAULTS), default="none",
                        help="serve a 500, a body that is not JSON, or JSON missing a record for the chosen read")
    parser.add_argument("--delay-data", type=float, default=0.0, dest="delay_data")
    parser.add_argument("--delay-refusals", type=float, default=0.0, dest="delay_refusals")
    args = parser.parse_args(argv)
    # absolute() rather than resolve(): the pack is opened and checked on every
    # request, so a missing or unreachable path shows on the page as the
    # reader's refusal instead of stopping the server at startup.
    OPTIONS.update(fault=args.fault, delay_data=args.delay_data, delay_refusals=args.delay_refusals, pack=args.pack.absolute())
    server = ThreadingHTTPServer(("127.0.0.1", args.port), InspectionHandler)
    print(f"inspection page: http://127.0.0.1:{server.server_port}/ (localhost only, no external calls)", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
