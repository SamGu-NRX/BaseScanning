#!/usr/bin/env python3
"""Localhost inspection server for capture evidence packs.

Serves the inspection page and the evidence it renders, reading the committed
pack and receipt files as the single source of truth. Binds 127.0.0.1 only and
makes no external network calls.

Usage:
    python tools/capture-evidence-pack/inspection/serve.py [--port 8793]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_DIR = Path(__file__).resolve().parents[1]
INSPECTION_DIR = Path(__file__).resolve().parent
PACK_PATH = TOOL_DIR / "evidence/example.zip"
REFUSAL_RECEIPTS = [
    ("missing-source", "receipts/read-refusal-missing-source.txt"),
    ("mismatched-result", "receipts/read-refusal-mismatched-result.txt"),
]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def evidence_data() -> dict:
    with zipfile.ZipFile(PACK_PATH) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        report = archive.read("report.md").decode("utf-8")
        entries = [
            {"path": info.filename, "bytes": info.file_size}
            for info in archive.infolist()
        ]
        manifest_sha256 = sha256_hex(archive.read("manifest.json"))
    return {
        "pack_path": "tools/capture-evidence-pack/evidence/example.zip",
        "pack_sha256": sha256_hex(PACK_PATH.read_bytes()),
        "manifest_sha256": manifest_sha256,
        "manifest": manifest,
        "report_markdown": report,
        "entries": entries,
    }


def refusal_data() -> dict:
    refusals = []
    for kind, relative in REFUSAL_RECEIPTS:
        path = TOOL_DIR / relative
        refusals.append(
            {
                "kind": kind,
                "receipt_path": f"tools/capture-evidence-pack/{relative}",
                "recorded_output": path.read_text(encoding="utf-8"),
            }
        )
    return {"refusals": refusals}


class InspectionHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/":
            self.send_file(INSPECTION_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/data.json":
            self.send_json(evidence_data())
        elif path == "/refusals.json":
            self.send_json(refusal_data())
        else:
            candidate = (INSPECTION_DIR / path.lstrip("/")).resolve()
            if candidate.is_file() and candidate.is_relative_to(INSPECTION_DIR):
                mime = "application/javascript" if candidate.suffix == ".js" else "text/plain; charset=utf-8"
                self.send_file(candidate, mime)
            else:
                self.send_error(404)

    def send_file(self, path: Path, mime: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # silence default stderr spam
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8793)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), InspectionHandler)
    print(f"inspection page: http://127.0.0.1:{args.port}/ (localhost only, no external calls)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
