"""Accept a packet, or keep the capture in awaiting_files until the listed bytes are committed."""

from __future__ import annotations

import gzip
import hashlib
import io
import json

from pydantic import ValidationError

from server.api.deps import Deps, extras, isoformat, save_extras
from server.api.errors import fail, pointer_from_loc
from server.api.hooks import safe_hook
from server.api.ids import require_contract_id
from server.api.keys import packet_key
from server.api.promotion import promote
from server.common.logging import log_event
from server.contracts.api import AmendmentRequest, FinalizeResponse
from server.contracts.jobs import CaptureRecord
from server.contracts.packet import Packet

_INFLATE_CAP = 48 * 1024 * 1024
_GZIP_CAP = 8 * 1024 * 1024
_TIER_MARKERS = (
    "ARKit",
    "photo_only",
    "arkit_lidar",
    "keyframes",
    "still",
    "lidarAvailable",
    "scale reference",
    "scaleReference",
    "meterCloseUp",
    "doorViews",
    "imuRaw",
    "epochs",
    "tracking",
    "worldAlignment",
    "videoFormat",
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run_id(data: bytes) -> str:
    return "run_" + _sha(data)[:16]


def inflate(body: bytes, encoding: str | None) -> bytes:
    if encoding and encoding != "gzip":
        raise fail(422, "schema", "/headers/content-encoding", "only gzip is accepted")
    if encoding == "gzip":
        if len(body) > _GZIP_CAP:
            raise fail(413, "payload_too_large", "/", "compressed body exceeds 8 MiB")
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as handle:
                inflated = handle.read(_INFLATE_CAP + 1)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise fail(422, "schema", "/", "gzip body could not be inflated") from exc
        if len(inflated) > _INFLATE_CAP:
            raise fail(413, "payload_too_large", "/", "inflated body exceeds 48 MiB")
        return inflated
    if len(body) > _INFLATE_CAP:
        raise fail(413, "payload_too_large", "/", "body exceeds 48 MiB")
    return body


def _schema_errors(exc: ValidationError) -> list[dict]:
    items = []
    for err in exc.errors():
        message = err.get("msg", "invalid")
        loc = tuple(err.get("loc") or ())
        if err.get("type") == "value_error" and any(marker in message for marker in _TIER_MARKERS):
            items.append({"code": "tier_rule", "pointer": "/session/tier", "message": message})
            continue
        items.append({"code": "schema", "pointer": pointer_from_loc(loc), "message": message})
    return items


def _compare_manifest(deps: Deps, record: CaptureRecord, packet: Packet) -> list[dict]:
    errors = []
    for index, entry in enumerate(packet.files):
        rec = deps.state.get_file(record.capture_id, entry.path)
        if rec is None:
            continue
        if rec.sha256 != entry.sha256:
            errors.append(
                {
                    "code": "sha256_mismatch",
                    "pointer": f"/files/{index}/sha256",
                    "message": "sha256 does not match the committed file",
                }
            )
        if rec.bytes != entry.bytes:
            errors.append(
                {
                    "code": "file_size_mismatch",
                    "pointer": f"/files/{index}/bytes",
                    "message": "size does not match the committed file",
                }
            )
    return errors


def render_finalize(status: str, missing: list[str], run_id: str) -> dict:
    eta = 200 if status == "awaiting_files" else 180
    return FinalizeResponse(status=status, missing=missing, runId=run_id, etaS=eta).model_dump(mode="json")


def _multi(errors: list[dict]):
    from server.api.errors import ApiFailure

    return ApiFailure(422, errors)


def accept_packet(
    deps: Deps,
    record: CaptureRecord,
    raw: bytes,
    *,
    amendment_id: str | None = None,
    parent_run_id: str | None = None,
    declared_sha: str | None = None,
) -> dict:
    from server.api.finalize_checks import manifest_errors

    digest = _sha(raw)
    if declared_sha is not None and declared_sha != digest:
        raise fail(422, "packet_sha_mismatch", "/headers/x-packet-sha256", "X-Packet-Sha256 does not match the body")
    if amendment_id is not None:
        amend = deps.state.get_kv(f"amend:{record.capture_id}:{amendment_id}") or {}
        if amend.get("packet_sha256") == digest:
            extra = extras(deps, record.capture_id)
            if amend.get("status") == "finalized" or extra.get("started_run_id") == amend.get("run_id"):
                return render_finalize("processing", [], amend.get("run_id") or record.run_id or _run_id(raw))
            missing = list(extra.get("missing") or [])
            run_id = amend.get("run_id") or _run_id(raw)
            if not missing:
                return finish_ready(
                    deps,
                    record,
                    digest,
                    packet_key(record.capture_id, amendment_id),
                    run_id,
                    parent_run_id=parent_run_id,
                )
            return render_finalize("awaiting_files", missing, run_id)
        if amend.get("packet_sha256") and amend.get("packet_sha256") != digest:
            raise fail(409, "packet_sha_conflict", "/", "a different amendment packet was already accepted")
    elif record.packet_sha256:
        if record.packet_sha256 == digest:
            extra = extras(deps, record.capture_id)
            missing = list(extra.get("missing") or [])
            run_id = record.run_id or _run_id(raw)
            if extra.get("run_started") or record.status == "processing":
                return render_finalize("processing", [], run_id)
            if not missing:
                return finish_ready(deps, record, digest, packet_key(record.capture_id, None), run_id)
            return render_finalize("awaiting_files", missing, run_id)
        raise fail(409, "packet_sha_conflict", "/", "a different packet was already accepted")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise fail(422, "schema", "/", "packet is not json") from exc
    if not isinstance(payload, dict):
        raise fail(422, "schema", "/", "packet must be an object")
    try:
        packet = Packet.model_validate(payload)
    except ValidationError as exc:
        errors = _schema_errors(exc)
        extra = extras(deps, record.capture_id)
        extra["last_422"] = [item["code"] for item in errors]
        save_extras(deps, record.capture_id, extra)
        log_event("finalize.rejected", codes=extra["last_422"], capture_id=record.capture_id)
        raise _multi(errors) from exc
    if packet.formatVersion != "0.4":
        raise fail(422, "format_version_unsupported", "/formatVersion", "only formatVersion 0.4 is accepted")
    require_contract_id(packet.packetId, owner=type(packet), field_name="packetId", pointer="/packetId")
    if amendment_id is not None:
        require_contract_id(amendment_id, owner=AmendmentRequest, field_name="amendmentId", pointer="/amendmentId")
    if amendment_id is None:
        if packet.packetId != record.packet_id:
            raise fail(409, "packet_id_mismatch", "/packetId", "packetId does not match the capture")
        if packet.parentPacketId:
            raise fail(422, "parent_unexpected", "/parentPacketId", "parentPacketId is only valid on an amendment")
    else:
        if packet.parentPacketId != record.packet_id:
            raise fail(422, "parent_mismatch", "/parentPacketId", "parentPacketId must be the capture packet id")
        if packet.packetId == record.packet_id:
            raise fail(422, "packet_id_reused", "/packetId", "amendment packetId must differ from the parent")
        if packet.formatVersion != "0.4":
            raise fail(422, "format_version_unsupported", "/formatVersion", "only formatVersion 0.4 is accepted")
    errors = (
        _compare_manifest(deps, record, packet)
        if amendment_id is None
        else _amendment_manifest(deps, record, packet, amendment_id)
    )
    errors.extend(
        {"code": item.code, "pointer": item.pointer, "message": item.message} for item in manifest_errors(packet)
    )
    if errors:
        extra = extras(deps, record.capture_id)
        extra["last_422"] = [item["code"] for item in errors]
        save_extras(deps, record.capture_id, extra)
        log_event("finalize.rejected", codes=extra["last_422"], capture_id=record.capture_id)
        raise _multi(errors)
    key = packet_key(record.capture_id, amendment_id)
    deps.storage.put_bytes(key, raw, "application/json")
    run_id = _run_id(raw)
    paths = [
        entry.path if amendment_id is None else f"amendments/{amendment_id}/{entry.path}" for entry in packet.files
    ]
    missing = []
    for path, entry in zip(paths, packet.files, strict=True):
        rec = deps.state.get_file(record.capture_id, path)
        if rec is None or rec.state != "committed":
            missing.append(entry.path)
    extra = extras(deps, record.capture_id)
    extra["accepted"] = {
        "packet_sha256": digest,
        "run_id": run_id,
        "at": isoformat(deps.now()),
        "listed": len(packet.files),
    }
    extra["paths"] = paths
    extra["digests"] = {
        path: {"sha256": entry.sha256, "bytes": entry.bytes} for path, entry in zip(paths, packet.files, strict=True)
    }
    if amendment_id is not None:
        extra["amendment_id"] = amendment_id
    extra["missing"] = missing
    extra.pop("last_422", None)
    if amendment_id is None:
        record.packet_sha256 = digest
        record.run_id = run_id
    else:
        extra["parent_run_id"] = parent_run_id
        amend = deps.state.get_kv(f"amend:{record.capture_id}:{amendment_id}") or {}
        amend["packet_sha256"] = digest
        amend["run_id"] = run_id
        if not missing:
            amend["status"] = "finalized"
            extra["open_amendment"] = None
        deps.state.put_kv(f"amend:{record.capture_id}:{amendment_id}", amend)
    save_extras(deps, record.capture_id, extra)
    log_event(
        "finalize.accepted", listed=len(packet.files), missing=len(missing), run_id=run_id, capture_id=record.capture_id
    )
    if missing:
        record.status = "awaiting_files"
        deps.state.put_capture(record)
        return render_finalize("awaiting_files", missing, run_id)
    return finish_ready(deps, record, digest, key, run_id, parent_run_id=parent_run_id)


def _amendment_manifest(deps: Deps, record: CaptureRecord, packet: Packet, amendment_id: str) -> list[dict]:
    errors = []
    prefix = f"amendments/{amendment_id}/"
    for index, entry in enumerate(packet.files):
        path = prefix + entry.path
        rec = deps.state.get_file(record.capture_id, path)
        if rec is None:
            continue
        if rec.sha256 != entry.sha256:
            errors.append(
                {
                    "code": "sha256_mismatch",
                    "pointer": f"/files/{index}/sha256",
                    "message": "sha256 does not match the committed file",
                }
            )
        if rec.bytes != entry.bytes:
            errors.append(
                {
                    "code": "file_size_mismatch",
                    "pointer": f"/files/{index}/bytes",
                    "message": "size does not match the committed file",
                }
            )
    return errors


def finish_ready(
    deps: Deps, record: CaptureRecord, digest: str, key: str, run_id: str, *, parent_run_id: str | None = None
) -> dict:
    extra = extras(deps, record.capture_id)
    if not extra.get("promoted_at"):
        promote(deps, record)
        record = deps.state.get_capture(record.capture_id) or record
        if record.status == "failed":
            raise fail(409, "invalid_state", "/", "promotion failed")
    if extra.get("started_run_id") != run_id:
        safe_hook("on_finalized", deps.hooks.on_finalized, record.capture_id, digest, key, parent_run_id=parent_run_id)
        log_event("run.started", run_id=run_id, capture_id=record.capture_id)
        extra = extras(deps, record.capture_id)
        extra["run_started"] = True
        extra["started_run_id"] = run_id
        extra["missing"] = []
        save_extras(deps, record.capture_id, extra)
    aid = extra.get("amendment_id")
    if aid:
        amend = deps.state.get_kv(f"amend:{record.capture_id}:{aid}") or {}
        amend["status"] = "finalized"
        amend["packet_sha256"] = digest
        amend["run_id"] = run_id
        deps.state.put_kv(f"amend:{record.capture_id}:{aid}", amend)
        extra["open_amendment"] = None
        save_extras(deps, record.capture_id, extra)
    current = record.model_copy(deep=True)
    stored = deps.state.get_capture(record.capture_id)
    if stored is not None and stored.status not in {"uploading", "awaiting_files"}:
        current.status = stored.status
        if stored.run_id:
            current.run_id = stored.run_id
    if current.status in {"uploading", "awaiting_files"}:
        current.status = "processing"
    if parent_run_id is not None:
        current.run_id = run_id
    elif not current.run_id:
        current.run_id = run_id
    deps.state.put_capture(current)
    deps.state.delete_kv(f"open:{record.capture_id}")
    # FinalizeResponse only names the accept states. A later status the orchestrator
    # already stored (manual_review, needs_views, complete) stays on the capture.
    shown = current.status if current.status in {"awaiting_files", "processing"} else "processing"
    return render_finalize(shown, [], current.run_id or run_id)


def maybe_finish_after_commit(deps: Deps, record: CaptureRecord) -> None:
    extra = extras(deps, record.capture_id)
    accepted = extra.get("accepted")
    if not accepted:
        return
    paths = list(extra.get("paths") or [])
    missing = []
    for path in paths:
        rec = deps.state.get_file(record.capture_id, path)
        if rec is None or rec.state != "committed":
            missing.append(path)
    extra["missing"] = missing
    save_extras(deps, record.capture_id, extra)
    if missing or extra.get("started_run_id") == accepted.get("run_id"):
        return
    finish_ready(
        deps,
        record,
        accepted["packet_sha256"],
        packet_key(record.capture_id, extra.get("amendment_id")),
        accepted["run_id"],
        parent_run_id=extra.get("parent_run_id"),
    )
