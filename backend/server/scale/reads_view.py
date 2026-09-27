"""Read a meter identity from WS-08's record. This stage never calls a vision model."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class MeterIdentity:
    manufacturer: str | None
    model_family: str | None
    form: str | None
    model_evidence: str | None
    model_confidence: float
    agree: bool
    images: list[str]
    cover_material: str
    socket_type: str
    ansi_socket_meter: bool | None
    base_flange_visible: bool | None
    type_code: str | None
    source: str


@dataclass
class IdentityMiss:
    code: str


def load_meter_identity(
    reads_json: Path | None,
    packet: Any,
    *,
    early_json: Path | None = None,
    min_confidence: float = 0.5,
) -> MeterIdentity | IdentityMiss:
    """Prefer ``reads.json``. Fall back to the early blob, then on-device text."""

    for path in (reads_json, early_json):
        if path is None or not Path(path).is_file():
            continue
        payload = json.loads(Path(path).read_text())
        parsed = _from_reads(payload, min_confidence)
        if parsed is not None:
            return parsed
        if _gave_nothing(payload):
            continue
        return parsed if isinstance(parsed, IdentityMiss) else IdentityMiss("meter_model_unread")
    on_device = _on_device(packet)
    if on_device is not None:
        return on_device
    return IdentityMiss("meter_model_unread")


def _gave_nothing(payload: dict) -> bool:
    if payload.get("status") == "disabled":
        return False
    return "meter_identity" not in payload


def _identity_record(payload: dict) -> tuple[dict, dict] | None:
    """Wrapped ``reads.json``, or WS-08's bare early ``MeterIdentity`` dump.

    The reads artifact nests the record under ``meter_identity``. The early
    blob is ``MeterIdentity.model_dump()`` itself: ``model_family`` at the top.
    """

    wrapped = payload.get("meter_identity")
    if isinstance(wrapped, dict):
        meter = payload.get("meter") or {}
        return wrapped, meter if isinstance(meter, dict) else {}
    if "model_family" in payload:
        return payload, {}
    return None


def _from_reads(payload: dict, min_confidence: float) -> MeterIdentity | IdentityMiss | None:
    if payload.get("status") == "disabled":
        return IdentityMiss("meter_model_unread")
    found = _identity_record(payload)
    if found is None:
        return None
    record, meter = found
    if not record.get("model_family"):
        return IdentityMiss("meter_model_unread")
    confidence = float(record.get("model_confidence") or 0.0)
    evidence = record.get("model_evidence")
    agree = bool(record.get("agree", False))
    if confidence < min_confidence or (not agree and evidence != "printed"):
        return IdentityMiss("meter_identity_low_confidence")
    images = [str(item) for item in (record.get("images") or [])]
    return MeterIdentity(
        manufacturer=record.get("manufacturer"),
        model_family=record.get("model_family"),
        form=meter.get("form") or record.get("form"),
        model_evidence=evidence,
        model_confidence=confidence,
        agree=agree,
        images=images,
        cover_material=str(record.get("cover_material") or "unknown"),
        socket_type=str(record.get("socket_type") or "unknown"),
        ansi_socket_meter=record.get("ansi_socket_meter"),
        base_flange_visible=record.get("base_flange_visible"),
        type_code=None,
        source="reads",
    )


def _on_device(packet: Any) -> MeterIdentity | None:
    scale_ref = _dig(packet, "scaleReference") or {}
    identity = scale_ref.get("meterIdentity") or {}
    if identity.get("source") != "on_device_text":
        return None
    make = identity.get("make")
    model = identity.get("model")
    if not make or not model:
        return None
    return MeterIdentity(
        manufacturer=str(make),
        model_family=str(model),
        form=None,
        model_evidence="printed",
        model_confidence=0.5,
        agree=True,
        images=[],
        cover_material=str(identity.get("cover_material") or "unknown"),
        socket_type="unknown",
        ansi_socket_meter=None,
        base_flange_visible=None,
        type_code=None,
        source="on_device_text",
    )


def _dig(packet: Any, key: str) -> Any:
    if isinstance(packet, dict):
        return packet.get(key)
    return getattr(packet, key, None)
