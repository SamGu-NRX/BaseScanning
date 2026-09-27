"""Contract double of the capture API. For kit tests and iOS development before the demo API."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import re
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from ios_kit.prompts import load_catalogue, render, select_prompt
from ios_kit.vendored import validate_packet

KIT_ROOT = Path(__file__).resolve().parent.parent
STAGES = (
    "validate",
    "poses",
    "objects",
    "reads",
    "scale",
    "dense",
    "coverage",
    "scene",
    "objects_3d",
    "criteria",
    "result",
)
ROLES = {
    "keyframe",
    "depth",
    "confidence",
    "features",
    "still",
    "stream",
    "truedepth",
    "mesh",
    "compat",
    "other",
}
# Same expressions as server.contracts._base (StorageId, RelPath).
_STORAGE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_SEGMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_REL_PATH = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9][A-Za-z0-9_-]*)*(/[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9][A-Za-z0-9_-]*)*)*$"
)
HINT_CONFIRM = {
    "imageId": "s1",
    "fieldId": "meter_number",
    "mode": "pick",
    "candidates": ["000000000", "111111111"],
    "preselected": "000000000",
    "allowNone": True,
}


def iso(when: datetime | None = None) -> str:
    moment = when or datetime.now(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def create_app(
    root: Path,
    *,
    result_kind: str = "needs_more_photos",
    token: str | None = None,
    verdict_delay_s: float = 5.0,
    stage_interval_s: float = 1.0,
    hint_delay_s: float = 2.0,
    capture_check_delay_s: float = 3.0,
) -> FastAPI:
    root.mkdir(parents=True, exist_ok=True)
    state = _State(root, result_kind, token, verdict_delay_s, stage_interval_s, hint_delay_s, capture_check_delay_s)
    app = FastAPI()
    app.state.kit = state

    @app.get("/v1/healthz")
    def healthz() -> dict:
        return {"status": "ok", "storage": "local", "state": "memory", "wifTokenExpiresAt": None, "version": "ios-kit"}

    @app.post("/v1/captures")
    async def create_capture(request: Request) -> JSONResponse:
        principal = _principal(request, token)
        if principal is None:
            return _error(401, "unauthorized", "", "bearer token required")
        body = await request.json()
        key = request.headers.get("idempotency-key", "")
        if body.get("formatVersion") != "0.4":
            return _error(422, "format_version_unsupported", "/formatVersion", "formatVersion must be 0.4")
        if not _is_storage_id(body.get("packetId")):
            return _error(422, "invalid_pattern", "/packetId", "packetId must match StorageId")
        canonical = json.dumps(body, sort_keys=True)
        if key and key in state.idempotency:
            previous, response = state.idempotency[key]
            if previous != canonical:
                return _error(409, "idempotency_conflict", "", "idempotency key reused with a different body")
            return JSONResponse(response, status_code=200)
        capture_id = "cap_" + uuid.uuid4().hex[:26]
        capture = _Capture(capture_id, body, key)
        state.captures[capture_id] = capture
        payload = {
            "captureId": capture_id,
            "status": "uploading",
            "upload": {
                "maxSinglePutBytes": 16777216,
                "partBytes": 8388608,
                "urlTtlS": 3600,
                "maxFiles": 2000,
                "maxBatch": 50,
            },
            "eventsUrl": f"/v1/captures/{capture_id}/events",
            "finalizeBy": iso(datetime.now(UTC) + timedelta(hours=24)),
        }
        if key:
            state.idempotency[key] = (canonical, payload)
        return JSONResponse(payload, status_code=201)

    @app.post("/v1/captures/{capture_id}/files")
    async def register_files(capture_id: str, request: Request) -> JSONResponse:
        if _principal(request, token) is None:
            return _error(401, "unauthorized", "", "bearer token required")
        capture = state.captures.get(capture_id)
        if capture is None:
            return _error(404, "not_found", "", "unknown capture")
        body = await request.json()
        registered = []
        for index, row in enumerate(body.get("files") or []):
            role = row.get("role")
            if role not in ROLES:
                return _error(422, "role_unknown", f"/files/{index}/role", f"unknown role {role}")
            path = row.get("path")
            if not _is_rel_path(path):
                return _error(422, "invalid_pattern", f"/files/{index}/path", "path must match RelPath")
            digest = row["sha256"]
            with capture.lock:
                current = capture.files.get(path)
                if current and current["sha256"] != digest:
                    return _error(409, "sha256_conflict", "/files/0/sha256", f"{path} already registered")
                if current and current["state"] == "committed":
                    registered.append({"path": path, "state": "committed", "upload": None})
                    continue
                upload_token = uuid.uuid4().hex
                capture.uploads[upload_token] = path
                capture.files[path] = {
                    "sha256": digest,
                    "md5": row.get("md5"),
                    "bytes": row.get("bytes"),
                    "role": role,
                    "state": "pending",
                    "data": current["data"] if current else None,
                }
            registered.append(
                {
                    "path": path,
                    "state": "pending",
                    "upload": {
                        "method": "PUT",
                        "url": f"/v1/uploads/{upload_token}",
                        "headers": {
                            "Content-Type": row.get("contentType", "application/octet-stream"),
                            "Content-MD5": row.get("md5", ""),
                        },
                        "expiresAt": iso(datetime.now(UTC) + timedelta(hours=1)),
                    },
                }
            )
        return JSONResponse({"files": registered})

    @app.put("/v1/uploads/{upload_token}")
    async def put_upload(upload_token: str, request: Request) -> Response:
        found = _find_upload(state, upload_token)
        if found is None:
            return Response("not found", status_code=404)
        capture, path = found
        data = await request.body()
        md5 = base64.b64encode(hashlib.md5(data).digest()).decode("ascii")
        expected = request.headers.get("content-md5")
        if expected and expected != md5:
            return Response("BadDigest", status_code=400)
        with capture.lock:
            slot = capture.files[path]
            slot["data"] = data
            slot["md5"] = md5
        (root / "uploads" / capture.capture_id / path).parent.mkdir(parents=True, exist_ok=True)
        (root / "uploads" / capture.capture_id / path).write_bytes(data)
        return Response(status_code=200)

    @app.post("/v1/captures/{capture_id}/files:commit")
    async def commit_files(capture_id: str, request: Request) -> JSONResponse:
        if _principal(request, token) is None:
            return _error(401, "unauthorized", "", "bearer token required")
        capture = state.captures.get(capture_id)
        if capture is None:
            return _error(404, "not_found", "", "unknown capture")
        body = await request.json()
        for index, row in enumerate(body.get("files") or []):
            if not _is_rel_path(row.get("path")):
                return _error(422, "invalid_pattern", f"/files/{index}/path", "path must match RelPath")
        committed, missing, mismatch = [], [], []
        first_keyframe = False
        meter_close = False
        for row in body.get("files") or []:
            path, digest = row["path"], row["sha256"]
            with capture.lock:
                slot = capture.files.get(path)
                if slot is None or slot.get("data") is None:
                    missing.append(path)
                    continue
                actual = hashlib.sha256(slot["data"]).hexdigest()
                if actual != digest or slot["sha256"] != digest:
                    mismatch.append(path)
                    continue
                slot["state"] = "committed"
                committed.append(path)
                if slot["role"] == "keyframe" and not capture.saw_keyframe:
                    capture.saw_keyframe = True
                    first_keyframe = True
                if _is_meter_close(capture, path):
                    meter_close = True
        if committed:
            _emit(
                capture,
                "files_committed",
                {"paths": committed, "committed": len(committed), "listed": len(body.get("files") or [])},
            )
        if first_keyframe:
            _later(capture_check_delay_s, lambda: _emit_capture_check(capture))
        packet_known = capture.packet is not None
        if meter_close or (packet_known and committed and not capture.packet_has_meter_close and not capture.hint_sent):
            _later(hint_delay_s, lambda: _emit_hint(capture))
        _maybe_start(state, capture)
        return JSONResponse({"committed": committed, "notFound": missing, "mismatch": mismatch})

    @app.post("/v1/captures/{capture_id}/finalize")
    async def finalize(capture_id: str, request: Request) -> JSONResponse:
        if _principal(request, token) is None:
            return _error(401, "unauthorized", "", "bearer token required")
        capture = state.captures.get(capture_id)
        if capture is None:
            return _error(404, "not_found", "", "unknown capture")
        raw = await request.body()
        if request.headers.get("content-encoding") == "gzip":
            raw = gzip.decompress(raw)
        digest = hashlib.sha256(raw).hexdigest()
        header = request.headers.get("x-packet-sha256", "")
        if header and header != digest:
            return _error(409, "packet_sha_conflict", "", "X-Packet-Sha256 does not match the body")
        if capture.packet_sha and capture.packet_sha != digest:
            return _error(409, "packet_sha_conflict", "", "packet bytes differ from the first finalize")
        packet = json.loads(raw)
        if packet.get("formatVersion") != "0.4":
            return _error(422, "format_version_unsupported", "/formatVersion", "formatVersion must be 0.4")
        problem = _packet_ids(packet)
        if problem is not None:
            return problem
        capture.packet = packet
        capture.packet_sha = digest
        capture.packet_has_meter_close = any(
            (row.get("purpose") == "meter_close") for row in packet.get("stills") or []
        )
        if not capture.packet_has_meter_close and not capture.hint_sent:
            _later(hint_delay_s, lambda: _emit_hint(capture))
        listed = [row["path"] for row in packet.get("files") or []]
        missing = [path for path in listed if capture.files.get(path, {}).get("state") != "committed"]
        run_id = "run_" + digest[:16]
        capture.run_id = run_id
        if missing:
            capture.status = "awaiting_files"
            return JSONResponse(
                {"status": "awaiting_files", "missing": missing, "runId": run_id, "etaS": 30}, status_code=202
            )
        problem = _validate_tree(state, capture, packet)
        if problem is not None:
            return problem
        _begin_processing(state, capture)
        return JSONResponse({"status": "processing", "missing": [], "runId": run_id, "etaS": 30}, status_code=202)

    @app.get("/v1/captures/{capture_id}/events")
    def events(capture_id: str, request: Request, after: int = 0, wait: int = 25) -> JSONResponse:
        if _principal(request, token) is None:
            return _error(401, "unauthorized", "", "bearer token required")
        capture = state.captures.get(capture_id)
        if capture is None:
            return _error(404, "not_found", "", "unknown capture")
        deadline = time.monotonic() + max(0, wait)
        while True:
            with capture.condition:
                fresh = [row for row in capture.events if row["seq"] > after]
                if fresh or time.monotonic() >= deadline:
                    payload = {
                        "status": capture.status,
                        "next": after if not capture.events else capture.events[-1]["seq"],
                        "events": fresh,
                    }
                    return JSONResponse(payload)
                capture.condition.wait(timeout=min(0.2, max(0.0, deadline - time.monotonic())))

    @app.get("/v1/captures/{capture_id}")
    def status(capture_id: str, request: Request) -> JSONResponse:
        if _principal(request, token) is None:
            return _error(401, "unauthorized", "", "bearer token required")
        capture = state.captures.get(capture_id)
        if capture is None:
            return _error(404, "not_found", "", "unknown capture")
        body = capture.create_body
        payload = {
            "captureId": capture.capture_id,
            "packetId": body.get("packetId", ""),
            "status": capture.status,
            "tier": body.get("tier", "arkit"),
            "flow": body.get("flow", "guided"),
            "createdAt": capture.created_at,
            "finalizeBy": iso(datetime.now(UTC) + timedelta(hours=24)),
            "filesRegistered": len(capture.files),
            "filesCommitted": sum(1 for row in capture.files.values() if row["state"] == "committed"),
            "runId": capture.run_id,
        }
        return JSONResponse(payload, headers={"ETag": capture.run_id or capture.capture_id})

    @app.get("/v1/captures/{capture_id}/result")
    def result(capture_id: str, request: Request) -> JSONResponse:
        if _principal(request, token) is None:
            return _error(401, "unauthorized", "", "bearer token required")
        capture = state.captures.get(capture_id)
        if capture is None or capture.result is None:
            return _error(404, "not_ready", "", "result is not ready")
        return JSONResponse(capture.result)

    @app.post("/v1/captures/{capture_id}/confirmations")
    async def confirm(capture_id: str, request: Request) -> JSONResponse:
        principal = _principal(request, token)
        if principal is None:
            return _error(401, "unauthorized", "", "bearer token required")
        capture = state.captures.get(capture_id)
        if capture is None:
            return _error(404, "not_found", "", "unknown capture")
        if capture.status in ("complete", "expired", "failed"):
            return _error(409, "capture_closed", "", "capture is closed")
        body = await request.json()
        if body.get("kind") != "none" and not body.get("value"):
            return _error(422, "value_required", "/value", "value is required unless kind is none")
        image_id = body.get("imageId")
        if image_id not in capture.known_images():
            return _error(422, "image_unknown", "/imageId", f"unknown image {image_id}")
        by = "reviewer" if principal.startswith("reviewer") else "device"
        field_id = body["fieldId"]
        signature = (body.get("kind"), body.get("value"), image_id)
        with capture.lock:
            previous = capture.confirmations.get(field_id)
            if previous and previous["signature"] == signature:
                return JSONResponse(previous["response"], status_code=200)
            if previous and previous["signature"] != signature and by != "reviewer":
                return _error(409, "confirmation_conflict", "/value", "a different answer is already stored")
            confirmation_id = "cfm_" + uuid.uuid4().hex[:26]
            response = {
                "confirmationId": confirmation_id,
                "fieldId": field_id,
                "imageId": image_id,
                "kind": body["kind"],
                "by": by,
                "receivedAt": iso(),
                "seq": 0,
            }
        event = _emit(
            capture,
            "confirmation",
            {
                "confirmationId": confirmation_id,
                "fieldId": field_id,
                "imageId": image_id,
                "kind": body["kind"],
                "by": by,
            },
        )
        response["seq"] = event["seq"]
        with capture.lock:
            capture.confirmations[field_id] = {"signature": signature, "response": response, "by": by}
        return JSONResponse(response, status_code=201)

    @app.get("/v1/captures/{capture_id}/confirmations")
    def list_confirmations(capture_id: str, request: Request) -> JSONResponse:
        if _principal(request, token) is None:
            return _error(401, "unauthorized", "", "bearer token required")
        capture = state.captures.get(capture_id)
        if capture is None:
            return _error(404, "not_found", "", "unknown capture")
        return JSONResponse({"confirmations": [row["response"] for row in capture.confirmations.values()]})

    @app.post("/v1/captures/{capture_id}/amendments")
    async def amendments(capture_id: str, request: Request) -> JSONResponse:
        if _principal(request, token) is None:
            return _error(401, "unauthorized", "", "bearer token required")
        if capture_id not in state.captures:
            return _error(404, "not_found", "", "unknown capture")
        body = await request.json()
        amendment_id = body.get("amendmentId")
        if not _is_storage_id(amendment_id):
            return _error(422, "invalid_pattern", "/amendmentId", "amendmentId must match StorageId")
        return JSONResponse(
            {"amendmentId": amendment_id, "pathPrefix": f"amendments/{amendment_id}/"},
            status_code=201,
        )

    return app


class _State:
    def __init__(
        self, root, result_kind, token, verdict_delay_s, stage_interval_s, hint_delay_s, capture_check_delay_s
    ):
        self.root = root
        self.result_kind = result_kind
        self.token = token
        self.verdict_delay_s = verdict_delay_s
        self.stage_interval_s = stage_interval_s
        self.captures: dict[str, _Capture] = {}
        self.idempotency: dict[str, tuple[str, dict]] = {}


class _Capture:
    def __init__(self, capture_id: str, create_body: dict, idempotency_key: str):
        self.capture_id = capture_id
        self.create_body = create_body
        self.idempotency_key = idempotency_key
        self.created_at = iso()
        self.status = "uploading"
        self.files: dict[str, dict] = {}
        self.uploads: dict[str, str] = {}
        self.events: list[dict] = []
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.packet: dict | None = None
        self.packet_sha: str | None = None
        self.run_id: str | None = None
        self.result: dict | None = None
        self.confirmations: dict[str, dict] = {}
        self.saw_keyframe = False
        self.hint_sent = False
        self.check_sent = False
        self.processing = False
        self.packet_has_meter_close = False
        self.hint_image_ids: set[str] = set()

    def known_images(self) -> set[str]:
        images = set(self.hint_image_ids)
        packet = self.packet or {}
        images.update(row.get("id") for row in packet.get("keyframes") or [] if row.get("id"))
        images.update(row.get("id") for row in packet.get("stills") or [] if row.get("id"))
        return images


def _principal(request: Request, expected: str | None) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return None
    got = header.removeprefix("Bearer ").strip()
    if not got:
        return None
    if expected and got != expected:
        return None
    return got


def _error(status: int, code: str, pointer: str, message: str) -> JSONResponse:
    return JSONResponse({"errors": [{"code": code, "pointer": pointer, "message": message}]}, status_code=status)


def _is_storage_id(value: object) -> bool:
    return isinstance(value, str) and _STORAGE_ID.fullmatch(value) is not None


def _is_rel_path(value: object) -> bool:
    return isinstance(value, str) and _REL_PATH.fullmatch(value) is not None


def _is_segment_id(value: object) -> bool:
    return isinstance(value, str) and _SEGMENT_ID.fullmatch(value) is not None


def _packet_ids(packet: dict) -> JSONResponse | None:
    if "packetId" in packet and not _is_storage_id(packet.get("packetId")):
        return _error(422, "invalid_pattern", "/packetId", "packetId must match StorageId")
    if packet.get("parentPacketId") is not None and not _is_storage_id(packet.get("parentPacketId")):
        return _error(422, "invalid_pattern", "/parentPacketId", "parentPacketId must match StorageId")
    for index, row in enumerate(packet.get("files") or []):
        if isinstance(row, dict) and not _is_rel_path(row.get("path")):
            return _error(422, "invalid_pattern", f"/files/{index}/path", "path must match RelPath")
    for name in ("keyframes", "stills"):
        for index, row in enumerate(packet.get(name) or []):
            if isinstance(row, dict) and "id" in row and not _is_segment_id(row.get("id")):
                return _error(422, "invalid_pattern", f"/{name}/{index}/id", "id must match SegmentId")
    mesh = packet.get("mesh")
    if isinstance(mesh, dict) and mesh.get("file") is not None and not _is_rel_path(mesh.get("file")):
        return _error(422, "invalid_pattern", "/mesh/file", "file must match RelPath")
    return None


def _find_upload(state: _State, upload_token: str) -> tuple[_Capture, str] | None:
    for capture in state.captures.values():
        path = capture.uploads.get(upload_token)
        if path:
            return capture, path
    return None


def _emit(capture: _Capture, event_type: str, data: dict) -> dict:
    with capture.condition:
        event = {"seq": len(capture.events) + 1, "type": event_type, "at": iso(), "data": data}
        capture.events.append(event)
        capture.condition.notify_all()
        return event


def _later(delay_s: float, fn) -> None:
    threading.Timer(delay_s, fn).start()


def _emit_hint(capture: _Capture) -> None:
    if capture.hint_sent:
        return
    capture.hint_sent = True
    capture.hint_image_ids.add(HINT_CONFIRM["imageId"])
    catalogue = load_catalogue()
    entry = select_prompt(catalogue, hint_code="meter_number_confirm")
    text = render(entry)
    _emit(
        capture,
        "hint",
        {
            "code": "meter_number_confirm",
            "message": text.body,
            "promptId": text.promptId,
            "confirm": dict(HINT_CONFIRM),
        },
    )


def _emit_capture_check(capture: _Capture) -> None:
    if capture.check_sent:
        return
    capture.check_sent = True
    packet = capture.packet or {}
    has_close = any(row.get("purpose") == "meter_close" for row in packet.get("stills") or [])
    doors = ((packet.get("scaleReference") or {}).get("ext") or {}).get("doorViews") or []
    missing = []
    if not has_close:
        missing.append("meter_close")
    if not doors:
        missing.append("door_view_meter_side")
    _emit(capture, "capture_check", {"complete": not missing, "missing": missing, "meterRead": "absent"})


def _is_meter_close(capture: _Capture, path: str) -> bool:
    packet = capture.packet
    if not packet:
        return False
    for still in packet.get("stills") or []:
        if still.get("img") == path and still.get("purpose") == "meter_close":
            return True
    return False


def _maybe_start(state: _State, capture: _Capture) -> None:
    if capture.packet is None or capture.processing:
        return
    listed = [row["path"] for row in (capture.packet.get("files") or [])]
    if listed and all(capture.files.get(path, {}).get("state") == "committed" for path in listed):
        if capture.status == "awaiting_files":
            problem = _validate_tree(state, capture, capture.packet)
            if problem is None:
                _begin_processing(state, capture)


def _begin_processing(state: _State, capture: _Capture) -> None:
    if capture.processing:
        return
    capture.processing = True
    capture.status = "processing"
    threading.Thread(target=_stages, args=(state, capture), daemon=True).start()


def _stages(state: _State, capture: _Capture) -> None:
    time.sleep(state.verdict_delay_s)
    for name in STAGES:
        _emit(
            capture,
            "stage",
            {"stage": name, "status": "done", "attempt": 1, "runId": capture.run_id, "durationS": 0.01},
        )
        time.sleep(state.stage_interval_s)
    result = _result_for(state, capture)
    kind = (result.get("outcome") or {}).get("kind", state.result_kind)
    _emit(capture, "verdict_ready", {"runId": capture.run_id, "kind": kind})
    capture.result = result
    capture.status = result.get("status", "complete")


def _result_for(state: _State, capture: _Capture) -> dict:
    path = KIT_ROOT / "examples" / "results" / f"{state.result_kind}.json"
    result = json.loads(path.read_text())
    result["runId"] = capture.run_id
    tier = (capture.create_body or {}).get("tier")
    if tier == "photo_only":
        _null_arkit(result)
    return result


def _null_arkit(value: object) -> None:
    if isinstance(value, dict):
        for key in list(value):
            if key in ("arkitEpoch", "frameToArkit") or key.endswith("Arkit") or "Arkit" in key:
                value[key] = None
            else:
                _null_arkit(value[key])
    elif isinstance(value, list):
        for child in value:
            _null_arkit(child)


def _validate_tree(state: _State, capture: _Capture, packet: dict) -> JSONResponse | None:
    folder = state.root / "captures" / capture.capture_id
    folder.mkdir(parents=True, exist_ok=True)
    for row in packet.get("files") or []:
        slot = capture.files.get(row["path"])
        if not slot or slot.get("data") is None:
            continue
        dest = folder / row["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(slot["data"])
    manifest = folder / "packet.json"
    manifest.write_text(json.dumps(packet))
    report = validate_packet(manifest, check_hashes=True)
    if report["ok"]:
        return None
    return _error_from_report(report)


def _error_from_report(report: dict) -> JSONResponse:
    errors = []
    for message in report["errors"]:
        if message.startswith("schema:"):
            rest = message[len("schema:") :].strip()
            pointer, _, detail = rest.partition(":")
            pointer = "/" + pointer if pointer and not pointer.startswith("/") else pointer
            errors.append({"code": "schema", "pointer": pointer or "", "message": detail.strip() or message})
        elif "sha256" in message:
            errors.append({"code": "sha256_mismatch", "pointer": "", "message": message})
        elif message.startswith("files:") and "missing" in message:
            errors.append({"code": "file_missing", "pointer": "", "message": message})
        elif "depth file" in message or "confidence file" in message:
            errors.append({"code": "depth_size", "pointer": "", "message": message})
        elif message.startswith("tap ") or "ray replay" in message:
            errors.append({"code": "tap_replay", "pointer": "", "message": message})
        elif message.startswith("stream ") or "CSV header" in message:
            errors.append({"code": "stream_header", "pointer": "", "message": message})
        else:
            errors.append({"code": "schema", "pointer": "", "message": message})
    return JSONResponse({"errors": errors}, status_code=422)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ios_kit.fake_api")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--result-kind", default="needs_more_photos")
    parser.add_argument("--verdict-delay-s", type=float, default=5.0)
    parser.add_argument("--stage-interval-s", type=float, default=1.0)
    parser.add_argument("--hint-delay-s", type=float, default=2.0)
    parser.add_argument("--capture-check-delay-s", type=float, default=3.0)
    parser.add_argument("--token", default=None)
    args = parser.parse_args(argv)
    app = create_app(
        args.root,
        result_kind=args.result_kind,
        token=args.token,
        verdict_delay_s=args.verdict_delay_s,
        stage_interval_s=args.stage_interval_s,
        hint_delay_s=args.hint_delay_s,
        capture_check_delay_s=args.capture_check_delay_s,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
