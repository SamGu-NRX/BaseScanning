"""Assemble per-still results into `base-reads/v0` and the capture's read status."""

from __future__ import annotations

import json
from pathlib import Path

from server.reads.cache import Storage, get_json, identity_key, put_json, reads_key, still_key
from server.reads.config import ReadsConfig
from server.reads.facts import to_criteria
from server.reads.types import (
    ConfirmBlock,
    MeterIdentity,
    MeterRead,
    PanelBundle,
    PanelCount,
    PanelRead,
    Reads,
    ReadsStatus,
    StillRef,
    StillResult,
)
from server.reads.worker import READ_PURPOSES, read_still

_OK = {"auto_fill", "prefill", "choose", "single_reader"}
_WEAK = {"unreadable", "disabled"}


def _load_stills(storage: Storage, capture_id: str) -> list[StillResult]:
    prefix = f"captures/{capture_id}/early/reads/"
    found: list[StillResult] = []
    for key in storage.list_prefix(prefix):
        if "/vlm-cache/" in key or not key.endswith(".json"):
            continue
        found.append(StillResult.model_validate(get_json(storage, key)))
    return found


def _purpose_of(record: dict) -> str | None:
    for key in ("still", "keyframe"):
        nested = record.get(key)
        if isinstance(nested, dict):
            purpose = nested.get("purpose")
            if isinstance(purpose, str):
                return purpose
    purpose = record.get("purpose")
    return purpose if isinstance(purpose, str) else None


def _stills_from_packet(packet: dict) -> list[dict]:
    rows = []
    for still in packet.get("stills") or []:
        if isinstance(still, dict):
            rows.append(still)
    for frame in packet.get("keyframes") or []:
        if isinstance(frame, dict) and frame.get("purpose") in READ_PURPOSES:
            rows.append(
                {
                    "id": frame.get("id"),
                    "purpose": frame.get("purpose"),
                    "keyframe": frame.get("id"),
                    "img": frame.get("img"),
                    "viewAngleDeg": frame.get("viewAngleDeg"),
                }
            )
    return rows


def _sha_by_path(packet: dict) -> dict[str, str]:
    found: dict[str, str] = {}
    for item in packet.get("files") or []:
        if isinstance(item, dict) and item.get("path") and item.get("sha256"):
            found[str(item["path"])] = str(item["sha256"])
    return found


def _still_bytes(still: dict, files: dict[str, str]) -> tuple[str | None, str | None]:
    """Contract stills use `img`; the digest lives on `files[]`. Older rows may carry path and sha256."""
    path = still.get("path") or still.get("key") or still.get("img")
    sha = still.get("sha256") or (files.get(str(path)) if path else None)
    if not path or not sha:
        return None, None
    return str(path), str(sha)


def _select_meter(items: list[StillResult]) -> StillResult | None:
    """Keep a legible earlier still unless a later one is auto_fill.

    A budget skip or an unreadable re-read must not hide a usable number.
    """
    chosen: StillResult | None = None
    for item in items:
        meter = item.meter
        if meter is None:
            continue
        if chosen is None or chosen.meter is None:
            chosen = item
            continue
        previous = chosen.meter
        if previous.status in _WEAK and meter.status not in _WEAK:
            chosen = item
        elif meter.status == "auto_fill":
            chosen = item
        elif previous.status == "disabled" and meter.status == "unreadable":
            chosen = item
    return chosen


def merge_meter(results: list[StillResult]) -> tuple[MeterRead | None, list[str], float]:
    close = [item for item in results if item.meter is not None and item.still.purpose == "meter_close"]
    oblique = [item for item in results if item.meter is not None and item.still.purpose == "meter_oblique"]
    warnings: list[str] = []
    close_pick = _select_meter(close)
    oblique_pick = _select_meter(oblique)
    primary = close_pick or oblique_pick
    if primary is None or primary.meter is None:
        return None, warnings, 0
    meter = primary.meter.model_copy(deep=True)
    images = [primary.still.still_id]
    median = 1.0
    close_meter = close_pick.meter if close_pick is not None else None
    oblique_meter = oblique_pick.meter if oblique_pick is not None else None
    if (
        close_pick is not None
        and oblique_pick is not None
        and close_meter is not None
        and oblique_meter is not None
        and close_meter.status in _WEAK
        and oblique_meter.status not in _WEAK
    ):
        meter = oblique_meter.model_copy(deep=True)
        images = [oblique_pick.still.still_id]
        warnings.append("value_from_oblique")
        median = 1.0
    elif (
        close_pick is not None
        and oblique_pick is not None
        and close_meter is not None
        and oblique_meter is not None
        and close_meter.status not in _WEAK
        and oblique_meter.status not in _WEAK
        and close_meter.core
        and close_meter.core == oblique_meter.core
        and close_meter.agree
        and oblique_meter.agree
    ):
        images = [close_pick.still.still_id, oblique_pick.still.still_id]
        median = 2.0
    meter.images = images
    if meter.cl320 == "cl320_meter":
        warnings.append("cl320_positive_unmeasured")
    return meter, warnings, median


def merge_panels(results: list[StillResult]) -> tuple[PanelRead | None, dict[str, PanelRead], PanelCount, list[str]]:
    panels = [item for item in results if item.panel is not None and item.still.purpose in READ_PURPOSES]
    if not panels:
        return None, {}, PanelCount(), []
    warnings: list[str] = []
    per_image = {item.still.still_id: item.panel for item in panels if item.panel is not None}
    confirmed = [item.panel for item in panels if item.panel is not None and item.panel.hazard == "confirmed"]
    disputed = [item.panel for item in panels if item.panel is not None and item.panel.hazard == "disputed"]
    base = (confirmed or disputed or [item.panel for item in panels if item.panel is not None])[0]
    merged = base.model_copy(deep=True) if base is not None else PanelRead()
    if confirmed:
        merged.hazard = "confirmed"
        merged.hazard_line = confirmed[0].hazard_line
    printed = [
        item.panel
        for item in panels
        if item.panel is not None and item.panel.brand and item.panel.brand_evidence == "printed_text"
    ]
    if printed and len({item.brand for item in printed}) > 1:
        merged.brand = None
        merged.brand_evidence = None
        warnings.append("brand_conflict")
    elif printed:
        source = printed[0]
        merged.brand = source.brand
        merged.brand_evidence = source.brand_evidence
        if source.legibility:
            merged.legibility = source.legibility
        merged.brand_mini = source.brand_mini or merged.brand_mini
        merged.brand_gpt = source.brand_gpt or merged.brand_gpt
        merged.evidence_mini = source.evidence_mini or merged.evidence_mini
        merged.evidence_gpt = source.evidence_gpt or merged.evidence_gpt
        merged.gpt_read = bool(source.gpt_read or merged.gpt_read)
        # A confirmed recall already owns the pair lines. A printed brand on another image must not replace them.
        if not confirmed:
            if source.hazard_line_mini:
                merged.hazard_line_mini = source.hazard_line_mini
            if source.hazard_line_gpt:
                merged.hazard_line_gpt = source.hazard_line_gpt
    amps = [item.panel.main_breaker_amps for item in panels if item.panel is not None and item.panel.main_breaker_amps]
    if amps and len(set(amps)) > 1:
        merged.main_breaker_amps = None
        warnings.append("amps_conflict")
    elif amps:
        merged.main_breaker_amps = amps[0]
    hints: list[str] = []
    for item in panels:
        if item.panel is None:
            continue
        for hint in item.panel.review_hints:
            if hint not in hints:
                hints.append(hint)
    merged.review_hints = hints
    enclosures = [item.panel.enclosures_visible for item in panels if item.panel and item.panel.enclosures_visible]
    mains = [item.panel.mains_visible for item in panels if item.panel and item.panel.mains_visible]
    enc_max = max(enclosures) if enclosures else None
    mains_max = max(mains) if mains else None
    giver = None
    for item in panels:
        if item.panel is None:
            continue
        if enc_max is not None and item.panel.enclosures_visible == enc_max:
            giver = item
        if mains_max is not None and item.panel.mains_visible == mains_max:
            giver = item
    agree = True
    if giver is not None:
        mini_gpt = {answer.model: answer for answer in giver.answers}
        if "gpt-5.4-mini" in mini_gpt and "gpt-5.5" in mini_gpt:
            mini_n = (mini_gpt["gpt-5.4-mini"].answer or {}).get("enclosures_visible")
            gpt_n = (mini_gpt["gpt-5.5"].answer or {}).get("enclosures_visible")
            agree = mini_n == gpt_n
    return merged, per_image, PanelCount(enclosures_max=enc_max, mains_max=mains_max, agree=agree), warnings


def _status_of(results: list[StillResult], kind: str) -> str:
    rows = [item for item in results if (item.meter if kind == "meter" else item.panel) is not None]
    if not rows:
        return "absent"
    if kind == "meter":
        states = [item.meter.status for item in rows if item.meter is not None]
        if any(state in _OK for state in states):
            return "ok"
        if any(state == "unreadable" for state in states):
            return "unreadable"
        return "pending"
    states = [item.panel.legibility for item in rows if item.panel is not None]
    if any(item.panel is not None and (item.panel.brand or item.panel.hazard == "confirmed") for item in rows):
        return "ok"
    if any(state == "poor" for state in states):
        return "unreadable"
    return "pending"


def reads_status(capture_id: str, storage: Storage) -> ReadsStatus:
    results = _load_stills(storage, capture_id)
    return ReadsStatus(meter=_status_of(results, "meter"), panel=_status_of(results, "panel"))  # type: ignore[arg-type]


def _confirm_block(meter: MeterRead | None) -> ConfirmBlock | None:
    if meter is None or meter.status not in {"prefill", "choose", "single_reader", "unreadable"}:
        return None
    image_id = meter.images[0] if meter.images else ""
    if meter.status == "unreadable":
        return ConfirmBlock(
            imageId=image_id,
            fieldId="meter_number",
            mode="type",
            candidates=[],
            preselected=None,
            allowNone=True,
        )
    return ConfirmBlock(
        imageId=image_id,
        fieldId="meter_number",
        mode="pick",
        candidates=[item.value for item in meter.candidates][:3],
        preselected=meter.value if meter.status in {"prefill", "single_reader"} else None,
        allowNone=True,
    )


def _identity(storage: Storage, capture_id: str, meter: MeterRead | None) -> MeterIdentity | None:
    key = identity_key(capture_id)
    if storage.exists(key):
        return MeterIdentity.model_validate(get_json(storage, key))
    if meter is None:
        return None
    return MeterIdentity(images=list(meter.images))


def build_reads(
    capture_id: str,
    storage: Storage,
    cfg: ReadsConfig,
    packet: dict | None = None,
) -> Reads:
    if packet is not None:
        files = _sha_by_path(packet)
        for still in _stills_from_packet(packet):
            purpose = _purpose_of(still) or still.get("purpose")
            if purpose not in READ_PURPOSES:
                continue
            still_id = str(still.get("id") or "")
            key = still_key(capture_id, still_id)
            if storage.exists(key):
                continue
            path, sha = _still_bytes(still, files)
            if not path or not sha:
                continue
            read_still(
                StillRef(
                    capture_id=capture_id,
                    still_id=still_id,
                    path=str(path),
                    sha256=str(sha),
                    purpose=str(purpose),
                    view_angle_deg=still.get("viewAngleDeg"),
                ),
                storage,
                cfg,
            )
    results = _load_stills(storage, capture_id)
    meter, meter_warnings, _median = merge_meter(results)
    panel, per_image, count, panel_warnings = merge_panels(results)
    warnings = meter_warnings + panel_warnings
    if any(answer.error == "not_evaluated" for item in results for answer in item.answers):
        warnings.append("budget_exhausted")
    disabled = bool(results) and all(answer.error == "disabled" for item in results for answer in item.answers)
    status = "disabled" if disabled else ("complete" if results else "partial")
    if results and any(item.meter is None and item.panel is None for item in results):
        status = "partial"
    reads = Reads(
        capture_id=capture_id,
        status=status,  # type: ignore[arg-type]
        meter=meter,
        meter_identity=_identity(storage, capture_id, meter),
        panel=PanelBundle(merged=panel, per_image=per_image) if panel is not None else None,
        panel_count=count if panel is not None else None,
        confirm=_confirm_block(meter),
        cost_usd=round(sum(item.cost_usd for item in results), 6),
        timings_s={item.still.still_id: item.secs for item in results},
        warnings=warnings,
    )
    reads.facts, reads.views_needed = to_criteria(reads, cfg)
    return reads


def fresh_still_result_key(capture_id: str, still_id: str) -> str:
    return still_key(capture_id, still_id)


def reads_attempt_rel(attempt: int) -> str:
    """Path under `StageContext.work_dir`. WS-03 uploads that file to `reads/reads.json`."""
    return f"_attempts/reads/attempt-{attempt}/reads.json"


def write_reads(
    capture_id: str,
    run_id: str,
    storage: Storage,
    reads: Reads,
    *,
    work_dir: Path,
    attempt: int,
) -> str:
    payload = reads.model_dump(mode="json")
    put_json(storage, reads_key(capture_id, run_id), payload)
    if reads.meter_identity is not None:
        put_json(storage, identity_key(capture_id), reads.meter_identity.model_dump(mode="json"))
    rel = reads_attempt_rel(attempt)
    dest = work_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(json.dumps(payload).encode())
    return rel
