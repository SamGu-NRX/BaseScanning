"""Packet validation stage. Entry point: `server.packet.run`."""

from __future__ import annotations

import json
import time
from pathlib import Path

from server.contracts.jobs import StageContext, StageResult
from server.packet.capture_check import capture_check
from server.packet.integrity import resolve_under
from server.packet.normalize import normalize_packet
from server.packet.report import PacketError, log_issues
from server.packet.validate import validate_packet

STAGE = "validate"


def run(ctx: StageContext) -> StageResult:
    ctx.heartbeat("load")
    packet_dir = _packet_dir(ctx)
    manifest = packet_dir / "packet.json"
    if not manifest.is_file():
        raise PacketError("io", message="packet.json is missing")
    ctx.heartbeat("validate")
    report = validate_packet(manifest, root=packet_dir, stage="full")
    run_prefix = f"captures/{ctx.capture_id}/runs/{ctx.run_id}"
    validation_key = f"{run_prefix}/packet/validation.json"
    _put_json(ctx, validation_key, report.model_dump(mode="json"))
    artifacts = {"packet/validation.json": validation_key}
    if not report.ok:
        log_issues(report, ctx.capture_id)
        raise PacketError("invalid", report=report, message="packet_invalid")
    ctx.heartbeat("normalize")
    started = time.perf_counter()
    normalized = normalize_packet(manifest, flow=getattr(ctx, "flow", None), report=report)
    normalize_s = time.perf_counter() - started
    ctx.heartbeat("capture_check")
    check = capture_check(normalized, mode="final", report_ok=report.ok)
    normalized.derived.captureCheck = check.model_dump(mode="json")
    normalized_key = f"{run_prefix}/packet/normalized.json"
    check_key = f"{run_prefix}/packet/capture_check.json"
    norm_path = Path(ctx.work_dir) / "normalized.json"
    normalized.to_json(norm_path)
    _put_bytes(ctx, normalized_key, norm_path.read_bytes(), "application/json")
    _put_json(ctx, check_key, check.model_dump(mode="json"))
    stills_dir = packet_dir / "normalized" / "stills"
    if stills_dir.is_dir():
        for path in stills_dir.glob("*.jpg"):
            key = f"captures/{ctx.capture_id}/normalized/stills/{path.name}"
            _put_bytes(ctx, key, path.read_bytes(), "image/jpeg")
    artifacts["packet/normalized.json"] = normalized_key
    artifacts["packet/capture_check.json"] = check_key
    codes = sorted({issue.code for issue in report.warnings})
    return StageResult(
        artifacts=artifacts,
        metrics={
            "validate_s": float(report.timingsS.get("total", 0.0)),
            "normalize_s": normalize_s,
            "keyframes": float(report.keyframes),
            "warnings": float(len(codes)),
        },
        warnings=codes,
    )


def _packet_dir(ctx) -> Path:
    given = (getattr(ctx, "inputs", None) or {}).get("packet_dir")
    if given:
        return Path(given)
    dest = Path(ctx.work_dir) / "packet"
    dest.mkdir(parents=True, exist_ok=True)
    prefix = f"captures/{ctx.capture_id}/"
    for key in ctx.storage.list_prefix(prefix):
        if not key.startswith(prefix):
            continue
        rel = key[len(prefix) :]
        if not rel or rel.startswith("runs/") or rel.startswith("normalized/"):
            continue
        target = resolve_under(dest, rel)
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ctx.storage.get_bytes(key))
    return dest


def _put_json(ctx, key: str, payload: dict) -> None:
    _put_bytes(ctx, key, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(), "application/json")


def _put_bytes(ctx, key: str, data: bytes, content_type: str) -> None:
    storage = ctx.storage
    if hasattr(storage, "put_bytes"):
        storage.put_bytes(key, data, content_type)
        return
    target = resolve_under(Path(ctx.work_dir), key)
    if target is None:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
