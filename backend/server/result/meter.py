"""Meter block for the device. Never log the meter value."""

from __future__ import annotations

import hashlib
import logging

from server.contracts.result import MeterBlock, MeterEvidence

logger = logging.getLogger("server.result.meter")

_PREFILL = {"prefill", "choose", "single_reader"}


def _digest(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def _clean(reads: dict) -> bool:
    for fact in reads.get("facts") or []:
        if fact.get("id") in {"meter_number", "meter"} and "clean" in fact:
            return bool(fact["clean"])
    meter = reads.get("meter") or {}
    return bool(meter.get("clean"))


def meter_block(reads: dict | None, *, autofill: str) -> MeterBlock:
    if not reads or not reads.get("meter") or (reads.get("meter") or {}).get("status") in {None, "disabled"}:
        if reads and (reads.get("meter") or {}).get("status") == "disabled":
            logger.info("meter status=%s value_sha256_8=%s", "not_captured", None)
            return MeterBlock(
                status="not_captured",
                value=None,
                candidates=[],
                barcodeConfirmed=False,
                readersAgree=False,
                clean=False,
            )
        if not reads or not reads.get("meter"):
            logger.info("meter status=%s value_sha256_8=%s", "not_captured", None)
            return MeterBlock(
                status="not_captured",
                value=None,
                candidates=[],
                barcodeConfirmed=False,
                readersAgree=False,
                clean=False,
            )

    meter = reads["meter"]
    raw = str(meter.get("status") or "")
    value = meter.get("value")
    candidates = [str(item["value"]) for item in (meter.get("candidates") or []) if item.get("value")][:3]
    agree = bool(meter.get("agree"))
    barcode = bool(meter.get("barcode_match"))
    if raw == "auto_fill" and autofill != "never":
        status = "auto_filled"
    elif raw == "auto_fill" or raw in _PREFILL:
        status = "prefill_confirm"
    elif raw == "unreadable":
        status = "unreadable"
    else:
        status = "not_captured"
    still = (reads.get("confirm") or {}).get("still_id")
    evidence = MeterEvidence(image=str(still), boxPx=None) if still else None
    logger.info("meter status=%s value_sha256_8=%s", status, _digest(value if isinstance(value, str) else None))
    shown = value if isinstance(value, str) else None
    if status == "not_captured":
        shown = None
    return MeterBlock(
        status=status,
        value=shown,
        candidates=candidates if status == "prefill_confirm" else candidates[:3],
        barcodeConfirmed=barcode,
        readersAgree=agree,
        clean=_clean(reads),
        evidence=evidence,
    )
