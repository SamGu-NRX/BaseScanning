#!/usr/bin/env python3
"""Upload one stored capture to the live stack and check the device result.

`make e2e-dev` runs this. SD-77: the only stack is Modal environment ``dev``
at ``https://huntertcarver-dev--capture-api.modal.run``, with captures in the
``house-scanning`` bucket. This script refuses any other origin.

SD-86: the demo stack does not use a bearer token. This client sends no
``Authorization`` header unless ``CAPTURE_API_TOKEN`` is set. A set token is
sent on API calls only and is never printed. Signed-URL uploads omit it.

Commit and finalize use a 900s read timeout and retry the same body. A 95-file
``files:commit`` can run for several minutes after the server has already
committed the paths; the old 150s timeout raised ``TimeoutError`` on that
commit. The commit route treats an already-committed path as success. Finalize
of the same packet returns the existing accept once the capture has a run, so
a lost 202 is not posted again.

Fixture meter pixels (dev server only, SD-97 is unchanged in prod):

``--meter-frames k00023:989.573,712.173 --anchor-label reference_point``

or ``HSS_E2E_METER_FRAMES`` and ``HSS_E2E_ANCHOR_LABEL``. Those become
``fixtureMeter`` on ``POST /v1/captures``. The server copies them onto the
coverage stage only when ``SMP_ENVIRONMENT=dev``.
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

from server.common.deploy_switch import REAL_RUN_CLIENT_WAIT_S
from server.contracts.result import Outcome, ResultResponse

# SD-77. One deployed stack. The origin is the Modal dev capture-api label.
LIVE_ORIGIN = "https://huntertcarver-dev--capture-api.modal.run"
TERMINAL = {"needs_views", "complete", "manual_review", "failed"}
# Reads other than commit and finalize. Create and a 50-file register fit.
READ_TIMEOUT_S = 150
# Capture-api allows 1200s. One 95-file commit has taken about 400s of Modal
# Dict writes, and the 150s read raised after the server had finished it.
PACKET_WRITE_TIMEOUT_S = 900
WRITE_ATTEMPTS = 3
_ACCEPTED = {"processing", "needs_views", "complete", "manual_review", "failed"}
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


def _pause(seconds: float) -> None:
    time.sleep(seconds)


def _retryable_transport(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError | urllib.error.URLError)


def _request(
    url: str,
    token: str,
    method: str,
    body: bytes | None = None,
    headers: dict | None = None,
    *,
    authorize: bool = True,
    timeout: float = READ_TIMEOUT_S,
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _json(
    url: str,
    token: str,
    method: str,
    payload: dict | None = None,
    headers: dict | None = None,
    *,
    timeout: float = READ_TIMEOUT_S,
) -> dict:
    raw = None if payload is None else json.dumps(payload).encode()
    extra = {"Content-Type": "application/json"} if payload is not None else {}
    if headers:
        extra.update(headers)
    status, data = _request(url, token, method, raw, extra or None, timeout=timeout)
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


def _content_type(role: str, path: str) -> str:
    if role == "stream":
        return "application/gzip"
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".heic":
        return "image/heic"
    return "application/octet-stream"


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


def create_payload(packet: dict, meter_frames: str = "", anchor_label: str = "") -> dict:
    """Create body. ``fixtureMeter`` is the dev-only replay path; prod ignores it."""
    packet_id = str(packet["packetId"])
    tier = (packet.get("session") or {}).get("tier") or "photo_only"
    flow = packet.get("flow") or ("photo" if tier == "photo_only" else "guided")
    body: dict = {
        "packetId": packet_id,
        "formatVersion": packet.get("formatVersion", "0.4"),
        "tier": tier,
        "flow": flow,
        "device": {"model": "e2e-dev", "systemVersion": "0", "appVersion": "0"},
    }
    frames = meter_frames.strip()
    anchor = anchor_label.strip()
    if anchor and not frames:
        raise SystemExit("anchor label needs --meter-frames (id:u,v pairs, commas between frames)")
    if frames:
        fixture: dict[str, str] = {"meterFrames": frames}
        if anchor:
            fixture["anchorLabel"] = anchor
        body["fixtureMeter"] = fixture
    return body


def commit_batch(api: str, token: str, capture_id: str, files: list[dict]) -> dict:
    """POST ``files:commit``. A timeout or 503 retries the same body.

    The server returns an already-committed path in ``committed`` and does not
    error, so a retry after a lost response is the same commit.
    """
    url = f"{api}/v1/captures/{capture_id}/files:commit"
    payload = {"files": [{"path": item["path"], "sha256": item["sha256"]} for item in files]}
    last: BaseException | None = None
    for attempt in range(WRITE_ATTEMPTS):
        try:
            return _json(url, token, "POST", payload, timeout=PACKET_WRITE_TIMEOUT_S)
        except ApiError as exc:
            last = exc
            if exc.status not in {503, 429} or attempt + 1 == WRITE_ATTEMPTS:
                raise
        except (TimeoutError, urllib.error.URLError) as exc:
            last = exc
            if not _retryable_transport(exc) or attempt + 1 == WRITE_ATTEMPTS:
                raise
        _pause(1)
    raise last or RuntimeError("commit failed")


def _capture_snapshot(api: str, token: str, capture_id: str) -> dict | None:
    try:
        return _json(f"{api}/v1/captures/{capture_id}", token, "GET", timeout=READ_TIMEOUT_S)
    except (ApiError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
        return None


def _finalize_already_accepted(snapshot: dict | None) -> bool:
    """True once finalize has a run, so a lost 202 must not be posted again."""
    if not snapshot:
        return False
    status = snapshot.get("status")
    if status in _ACCEPTED:
        return True
    return bool(snapshot.get("runId")) and status not in {"uploading", None}


def finalize_capture(api: str, token: str, capture_id: str, raw: bytes, packet_id: str) -> dict:
    """POST finalize. Timeout and 503 retry the same packet unless a run exists."""
    url = f"{api}/v1/captures/{capture_id}/finalize"
    headers = {
        "Idempotency-Key": packet_id,
        "Content-Type": "application/json",
        "X-Packet-Sha256": _sha256(raw),
    }
    last: BaseException | None = None
    for attempt in range(WRITE_ATTEMPTS):
        try:
            status, data = _request(url, token, "POST", raw, headers, timeout=PACKET_WRITE_TIMEOUT_S)
        except (TimeoutError, urllib.error.URLError) as exc:
            last = exc
            snapshot = _capture_snapshot(api, token, capture_id)
            if snapshot is not None and _finalize_already_accepted(snapshot):
                print(f"finalize already accepted status {snapshot.get('status')} runId {snapshot.get('runId')}")
                return snapshot
            if not _retryable_transport(exc) or attempt + 1 == WRITE_ATTEMPTS:
                raise
            _pause(1)
            continue
        if status < 400:
            if not data:
                return {}
            parsed = json.loads(data)
            if not isinstance(parsed, dict):
                raise ApiError(status, "response was not an object")
            return parsed
        snapshot = _capture_snapshot(api, token, capture_id)
        if snapshot is not None and _finalize_already_accepted(snapshot):
            print(f"finalize already accepted status {snapshot.get('status')} runId {snapshot.get('runId')}")
            return snapshot
        if status in {503, 429} and attempt + 1 < WRITE_ATTEMPTS:
            _pause(1)
            continue
        raise ApiError(status, data.decode(errors="replace"))
    raise last or RuntimeError("finalize failed")


def upload(
    api: str,
    token: str,
    root: Path,
    packet: dict,
    *,
    meter_frames: str = "",
    anchor_label: str = "",
) -> str:
    packet_id = str(packet["packetId"])
    created = _json(
        f"{api}/v1/captures",
        token,
        "POST",
        create_payload(packet, meter_frames, anchor_label),
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
        role = entry.get("role") or "still"
        files.append(
            {
                "path": rel,
                "bytes": len(data),
                "sha256": _sha256(data),
                "md5": _md5_b64(data),
                "role": role,
                "contentType": _content_type(role, rel),
            }
        )
    batch_size = min(50, (created.get("upload") or {}).get("maxBatch", 50))
    for start in range(0, len(files), batch_size):
        batch = files[start : start + batch_size]
        registered = _json(f"{api}/v1/captures/{capture_id}/files", token, "POST", {"files": batch})
        for item in registered.get("files") or []:
            if item["state"] == "committed":
                continue
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
        commit_batch(api, token, capture_id, batch)
    raw = json.dumps(packet).encode()
    print(f"capture {capture_id} finalizing")
    accepted = finalize_capture(api, token, capture_id, raw, packet_id)
    print(accepted)
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


def live(
    api: str,
    token: str,
    packet_path: Path,
    timeout_s: float,
    *,
    meter_frames: str = "",
    anchor_label: str = "",
) -> int:
    root, packet = resolve_packet(packet_path)
    started = time.monotonic()
    capture_id = upload(
        api.rstrip("/"),
        token,
        root,
        packet,
        meter_frames=meter_frames,
        anchor_label=anchor_label,
    )
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


def parser() -> argparse.ArgumentParser:
    built = argparse.ArgumentParser(description="Live-stack capture upload and result-schema check")
    built.add_argument("--self-check", action="store_true")
    built.add_argument("--packet", type=Path)
    built.add_argument("--timeout", type=float, default=float(REAL_RUN_CLIENT_WAIT_S))
    built.add_argument(
        "--meter-frames",
        default="",
        help="Dev-only fixture pixels, id:u,v pairs (or HSS_E2E_METER_FRAMES). Prod ignores them.",
    )
    built.add_argument(
        "--anchor-label",
        default="",
        help="Dev-only tap label, such as reference_point (or HSS_E2E_ANCHOR_LABEL).",
    )
    return built


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
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
    meter_frames = args.meter_frames or os.environ.get("HSS_E2E_METER_FRAMES", "")
    anchor_label = args.anchor_label or os.environ.get("HSS_E2E_ANCHOR_LABEL", "")
    return live(
        api,
        token,
        args.packet,
        args.timeout,
        meter_frames=meter_frames,
        anchor_label=anchor_label,
    )


if __name__ == "__main__":
    sys.exit(main())
