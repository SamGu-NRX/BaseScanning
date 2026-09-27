#!/usr/bin/env python3
"""Upload one stored capture to the live stack and check the device result.

`make e2e-dev` runs this. SD-77: the only stack is Modal environment ``dev``
at ``https://huntertcarver-dev--capture-api.modal.run``, with captures in the
``house-scanning`` bucket. This script refuses any other origin.

SD-86: the demo stack does not use a bearer token. This client sends no
``Authorization`` header unless ``CAPTURE_API_TOKEN`` is set. A set token is
sent on API calls only and is never printed. Signed-URL uploads omit it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from pydantic import ValidationError

from server.contracts.result import Outcome, ResultResponse

# SD-77. One deployed stack. The origin is the Modal dev capture-api label.
LIVE_ORIGIN = "https://huntertcarver-dev--capture-api.modal.run"
TERMINAL = {"needs_views", "complete", "manual_review", "failed"}
STUB_RESULT = {
    "outcome": {"kind": "manual_review", "profile": "C"},
    "verdictSha256": "abc",
    "status": "processing",
    "runId": "run_example",
}


def minimal_result() -> dict:
    body = ResultResponse(
        runId="run_example",
        status="manual_review",
        viewsNeeded=[],
        memberActions=[],
        outcome=Outcome(
            kind="manual_review",
            profile="C",
            message="reviewed",
            viewsNeeded=[],
            reasons=[],
        ),
    )
    return body.model_dump(mode="json")


def schema_errors(payload: object) -> list[str]:
    try:
        result = ResultResponse.model_validate(payload)
    except ValidationError as exc:
        return [f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()]
    if result.outcome is None:
        return ["outcome is null"]
    if result.status not in TERMINAL:
        return [f"status {result.status} is not a finished capture"]
    return []


def self_check() -> int:
    good = schema_errors(minimal_result())
    bad = schema_errors(STUB_RESULT)
    if good:
        print("self-check: minimal ResultResponse failed")
        for item in good:
            print(f"  {item}")
        return 1
    if not bad:
        print("self-check: stub result shape was accepted")
        return 1
    print(f"self-check: ok ({len(bad)} errors on the stub result shape)")
    return 0


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status


def _request_headers(token: str, headers: dict | None = None) -> dict[str, str]:
    """SD-86: omit Authorization when the demo stack is running with auth off."""
    req_headers: dict[str, str] = {}
    if token:
        req_headers["Authorization"] = f"Bearer {token}"
    if headers:
        req_headers.update(headers)
    return req_headers


def _request(
    url: str,
    token: str,
    method: str,
    body: bytes | None = None,
    headers: dict | None = None,
    *,
    authorize: bool = True,
) -> tuple[int, bytes]:
    # Signed object-storage URLs authenticate themselves. A bearer token on
    # that PUT is not an API call and storage rejects the extra header.
    request = urllib.request.Request(
        url,
        data=body,
        headers=_request_headers(token if authorize else "", headers),
        method=method,
    )
    try:
        # Finalize runs the in-process orchestrator before it returns 202.
        # The capture-api function allows 120s, so the read has to outlast that.
        with urllib.request.urlopen(request, timeout=150) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _json(url: str, token: str, method: str, payload: dict | None = None, headers: dict | None = None) -> dict:
    raw = None if payload is None else json.dumps(payload).encode()
    extra = {"Content-Type": "application/json"} if payload is not None else {}
    if headers:
        extra.update(headers)
    status, data = _request(url, token, method, raw, extra or None)
    if status >= 400:
        raise ApiError(status, data.decode(errors="replace"))
    if not data:
        return {}
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise ApiError(status, "response was not an object")
    return parsed


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _md5_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.md5(data).digest()).decode()


def resolve_packet(path: Path) -> tuple[Path, dict]:
    if path.is_dir():
        candidate = path / "packet.json"
        if not candidate.is_file():
            raise SystemExit(f"no packet.json in {path}")
        path = candidate
    packet = json.loads(path.read_text())
    if not isinstance(packet, dict):
        raise SystemExit("packet is not an object")
    return path.parent, packet


def upload(api: str, token: str, root: Path, packet: dict) -> str:
    packet_id = str(packet["packetId"])
    tier = (packet.get("session") or {}).get("tier") or "photo_only"
    flow = packet.get("flow") or ("photo" if tier == "photo_only" else "guided")
    created = _json(
        f"{api}/v1/captures",
        token,
        "POST",
        {
            "packetId": packet_id,
            "formatVersion": packet.get("formatVersion", "0.4"),
            "tier": tier,
            "flow": flow,
            "device": {"model": "e2e-dev", "systemVersion": "0", "appVersion": "0"},
        },
        {"Idempotency-Key": packet_id},
    )
    capture_id = str(created["captureId"])
    files = []
    blobs: dict[str, bytes] = {}
    for entry in packet.get("files") or []:
        rel = str(entry["path"])
        data = (root / rel).read_bytes()
        blobs[rel] = data
        entry["sha256"] = _sha256(data)
        entry["bytes"] = len(data)
        files.append(
            {
                "path": rel,
                "bytes": len(data),
                "sha256": _sha256(data),
                "md5": _md5_b64(data),
                "role": entry.get("role") or "still",
                "contentType": "image/jpeg" if rel.endswith((".jpg", ".jpeg")) else "application/octet-stream",
            }
        )
    if files:
        registered = _json(f"{api}/v1/captures/{capture_id}/files", token, "POST", {"files": files})
        for item in registered.get("files") or []:
            upload_info = item["upload"]
            status, data = _request(
                upload_info["url"],
                token,
                upload_info.get("method", "PUT"),
                blobs[item["path"]],
                upload_info.get("headers") or {},
                authorize=False,
            )
            if status >= 400:
                raise ApiError(status, data.decode(errors="replace"))
        _json(
            f"{api}/v1/captures/{capture_id}/files:commit",
            token,
            "POST",
            {"files": [{"path": rel, "sha256": _sha256(blob)} for rel, blob in blobs.items()]},
        )
    raw = json.dumps(packet).encode()
    print(f"capture {capture_id} finalizing")
    status, data = _request(
        f"{api}/v1/captures/{capture_id}/finalize",
        token,
        "POST",
        raw,
        {
            "Idempotency-Key": packet_id,
            "Content-Type": "application/json",
            "X-Packet-Sha256": _sha256(raw),
        },
    )
    if status >= 400:
        raise ApiError(status, data.decode(errors="replace"))
    print(json.loads(data))
    return capture_id


def poll_result(api: str, token: str, capture_id: str, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = _json(f"{api}/v1/captures/{capture_id}/result", token, "GET")
        if last.get("outcome") is not None and last.get("status") in TERMINAL:
            return last
        time.sleep(2)
    return last


def live(api: str, token: str, packet_path: Path, timeout_s: float) -> int:
    root, packet = resolve_packet(packet_path)
    started = time.monotonic()
    capture_id = upload(api.rstrip("/"), token, root, packet)
    payload = poll_result(api.rstrip("/"), token, capture_id, timeout_s)
    elapsed = time.monotonic() - started
    errors = schema_errors(payload)
    print(f"capture {capture_id} elapsed_s {elapsed:.1f} status {payload.get('status')}")
    if errors:
        print("result schema failed")
        for item in errors:
            print(f"  {item}")
        return 1
    print("result schema ok")
    print("stage timings: not in the device result; read run/timings.json from the run artifacts")
    return 0


def live_origin(raw: str) -> str | None:
    """Return the live capture-api origin, or None when the URL is some other stack."""
    url = raw.strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[: -len("/v1")].rstrip("/")
    if url != LIVE_ORIGIN:
        return None
    return url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live-stack capture upload and result-schema check")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()
    raw_api = os.environ.get("CAPTURE_API_URL", "").strip()
    token = os.environ.get("CAPTURE_API_TOKEN", "").strip()
    api = live_origin(raw_api) if raw_api else None
    if raw_api and api is None:
        print(f"make e2e-dev talks only to the live stack: {LIVE_ORIGIN}")
        print("SD-77: that Modal dev capture-api and the house-scanning bucket are the whole deployment.")
        return 2
    if not api or args.packet is None:
        print(f"set CAPTURE_API_URL={LIVE_ORIGIN} and --packet")
        print("SD-86: the demo stack takes no Authorization header and no CAPTURE_API_TOKEN.")
        print("schema self-check: python scripts/e2e_dev.py --self-check")
        return 2
    return live(api, token, args.packet, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
