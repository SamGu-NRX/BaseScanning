"""Meter decision: agreement plus a barcode match is the only auto-fill."""

from __future__ import annotations

from typing import Literal

from server.reads.barcode import payload_tokens
from server.reads.candidates import class_number, implausible_flags, normalize, ranked_values
from server.reads.candidates import core as core_of
from server.reads.config import ReadsConfig
from server.reads.facts import to_criteria
from server.reads.panel_policy import as_int
from server.reads.types import (
    Candidate,
    ConfirmationConflict,
    ConfirmBlock,
    MeterRead,
    ReaderAnswer,
    Reads,
    ReadsError,
)

# Measured rates. auto_fill is the barcode-confirmed pair, not the 65/68 agreement rate.
CONFIDENCE = {
    "auto_fill": 0.99,
    "prefill_agree": 0.956,
    "prefill_barcode": 0.95,
    "choose": 0.5,
    "gpt-5.5": 0.95,
    "grok-4.7": 0.90,
    "confirmed": 1.0,
}

GPT = "gpt-5.5"
GROK = "grok-4.7"


def _answer_of(reader: ReaderAnswer | None) -> dict | None:
    if reader is None or reader.error or not reader.answer:
        return None
    return reader.answer


def _failed(reader: ReaderAnswer | None) -> bool:
    return reader is None or reader.error is not None


def _printed_for(pairs: list[tuple[str, str]], token: str) -> str:
    for printed, core_value in pairs:
        if core_value == token:
            return printed.strip()
    return token


def _merge_sources(
    gpt_pairs: list[tuple[str, str]],
    grok_pairs: list[tuple[str, str]],
    barcodes: set[str],
) -> list[Candidate]:
    order: list[str] = []
    printed: dict[str, str] = {}
    sources: dict[str, list[str]] = {}

    def add(pairs: list[tuple[str, str]], source: str, only_first: bool = False) -> None:
        for index, (text, token) in enumerate(pairs):
            if only_first and index > 0:
                continue
            if token not in printed:
                printed[token] = text.strip()
                order.append(token)
                sources[token] = []
            if source not in sources[token]:
                sources[token].append(source)

    if gpt_pairs:
        add(gpt_pairs, GPT, only_first=True)
    if grok_pairs:
        add(grok_pairs, GROK, only_first=True)
    add(gpt_pairs, GPT)
    add(grok_pairs, GROK)
    for token in barcodes:
        if token in sources and "barcode" not in sources[token]:
            sources[token].append("barcode")
    # Demote implausible cores but keep them.
    ranked = sorted(order, key=lambda token: (bool(implausible_flags(printed[token], token)), order.index(token)))
    return [
        Candidate(
            value=printed[token],
            core=token,
            sources=sources[token],
            rank=index + 1,
            flags=implausible_flags(printed[token], token),
        )
        for index, token in enumerate(ranked)
    ]


def _legibility(gpt: ReaderAnswer | None, grok: ReaderAnswer | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for reader in (gpt, grok):
        if reader is None or not reader.answer:
            continue
        label = reader.answer.get("legibility")
        if isinstance(label, str):
            out[reader.model] = label
    return out


def _class_fields(gpt_label: str | None, grok_label: str | None) -> tuple[str | None, str | None, str]:
    gpt_num = class_number(gpt_label)
    grok_num = class_number(grok_label)
    if gpt_num == "CL320" and grok_num == "CL320":
        return gpt_label, "CL320", "cl320_meter"
    if gpt_num and grok_num and gpt_num == grok_num:
        return gpt_label, gpt_num, "not_cl320_meter"
    if (
        gpt_label
        and grok_label
        and normalize(gpt_label) == normalize(grok_label)
        and gpt_num is None
        and grok_num is None
    ):
        return gpt_label, None, "not_cl320_meter"
    label = gpt_label or grok_label
    return label, class_number(label), "cl320_unclear"


def _agreed_text(gpt_answer: dict | None, grok_answer: dict | None, key: str) -> tuple[str | None, bool]:
    gpt_value = (gpt_answer or {}).get(key)
    grok_value = (grok_answer or {}).get(key)
    gpt_text = str(gpt_value).strip() if gpt_value else None
    grok_text = str(grok_value).strip() if grok_value else None
    if gpt_text and grok_text and normalize(gpt_text) == normalize(grok_text):
        return gpt_text, True
    return gpt_text or grok_text, False


def decide_meter(
    gpt: ReaderAnswer | None,
    grok: ReaderAnswer | None,
    barcodes: list[dict],
    cfg: ReadsConfig,
) -> MeterRead:
    gpt_answer = _answer_of(gpt)
    grok_answer = _answer_of(grok)
    gpt_pairs = ranked_values(gpt_answer)
    grok_pairs = ranked_values(grok_answer)
    barcode_cores = payload_tokens(barcodes)
    g = gpt_pairs[0][1] if gpt_pairs else None
    x = grok_pairs[0][1] if grok_pairs else None
    both_disabled = (gpt is not None and gpt.error == "disabled") and (grok is not None and grok.error == "disabled")
    both_skipped = (gpt is not None and gpt.error == "not_evaluated") and (
        grok is not None and grok.error == "not_evaluated"
    )
    candidates = _merge_sources(gpt_pairs, grok_pairs, barcode_cores)[:3]
    label, class_num, cl320 = _class_fields(
        (gpt_answer or {}).get("class_label") if gpt_answer else None,
        (grok_answer or {}).get("class_label") if grok_answer else None,
    )
    form, form_agree = _agreed_text(gpt_answer, grok_answer, "form")
    manufacturer, mfr_agree = _agreed_text(gpt_answer, grok_answer, "manufacturer")
    common = {
        "candidates": candidates,
        "class_label": label,
        "class_number": class_num,
        "cl320": cl320,
        "form": form,
        "manufacturer": manufacturer,
        "legibility": _legibility(gpt, grok),
    }
    del form_agree, mfr_agree

    if both_disabled or both_skipped:
        return MeterRead(status="disabled", **common)

    def finish(status: str, token: str | None, confidence: float, agree: bool, barcode_match: bool) -> MeterRead:
        printed = None
        if token:
            printed = (
                _printed_for(gpt_pairs, token)
                if token in {c for _, c in gpt_pairs}
                else _printed_for(grok_pairs, token)
            )
            if token not in {item.core for item in candidates}:
                printed = _printed_for(gpt_pairs + grok_pairs, token)
        return MeterRead(
            status=status,  # type: ignore[arg-type]
            value=printed,
            core=token,
            confidence=confidence,
            agree=agree,
            barcode_match=barcode_match,
            **common,
        )

    if g and x and g == x and g in barcode_cores:
        return finish("auto_fill", g, CONFIDENCE["auto_fill"], True, True)
    if g and x and g == x:
        status = "auto_fill" if cfg.autofill_without_barcode else "prefill"
        confidence = CONFIDENCE["auto_fill"] if status == "auto_fill" else CONFIDENCE["prefill_agree"]
        return finish(status, g, confidence, True, False)

    gpt_hit = bool(g) and g in barcode_cores
    grok_hit = bool(x) and x in barcode_cores
    if gpt_hit ^ grok_hit and (_failed(gpt) or _failed(grok) or (g and x and g != x) or (not g) or (not x)):
        token = g if gpt_hit else x
        return finish("prefill", token, CONFIDENCE["prefill_barcode"], False, True)

    if g and x and g != x:
        return finish("choose", None, CONFIDENCE["choose"], False, False)

    if gpt_pairs and _failed(grok):
        return finish("single_reader", gpt_pairs[0][1], CONFIDENCE[GPT], False, False)
    if grok_pairs and _failed(gpt):
        return finish("single_reader", grok_pairs[0][1], CONFIDENCE[GROK], False, False)
    if gpt_pairs and not grok_pairs and grok is not None and grok.error is None:
        return finish("single_reader", gpt_pairs[0][1], CONFIDENCE[GPT], False, False)
    if grok_pairs and not gpt_pairs and gpt is not None and gpt.error is None:
        return finish("single_reader", grok_pairs[0][1], CONFIDENCE[GROK], False, False)
    return finish("unreadable", None, 0.0, False, False)


def _same_answer(reads: Reads, field_id: str, choice: str | None, typed: str | None) -> bool:
    """SD-52: the same device answer is idempotent; a different one conflicts."""
    if field_id == "meter_number":
        meter = reads.meter
        if meter is None:
            return False
        if choice is None and typed is None:
            return meter.status == "unreadable"
        incoming = typed if typed is not None else choice
        return meter.value == incoming
    panel = reads.panel.merged if reads.panel is not None else None
    if panel is None or (choice is None and typed is None):
        return False
    incoming = typed if typed is not None else choice
    if field_id == "panel_brand":
        return panel.brand == incoming
    if field_id == "main_breaker_amps":
        return as_int(incoming) is not None and as_int(incoming) == panel.main_breaker_amps
    return False


def _candidate_values(reads: Reads) -> list[str]:
    if reads.meter is None:
        return []
    return [item.value for item in reads.meter.candidates]


def _stamp_confirm(
    block: ConfirmBlock,
    still_id: str,
    field_id: Literal["meter_number", "panel_brand", "main_breaker_amps"],
    choice: str | None,
    typed: str | None,
    by: Literal["device", "reviewer"],
    candidates: list[str],
) -> None:
    """Write the stored reply with `HintData.confirm` names (`pick|type|none`, string preselected)."""
    block.imageId = still_id
    block.fieldId = field_id
    block.allowNone = True
    block.answered_by = by
    block.locks[field_id] = by
    if choice is None and typed is None:
        block.mode = "none"
        block.preselected = None
        block.answer = None
        block.candidates = []
        return
    if typed is not None:
        block.mode = "type"
        block.preselected = None
        block.candidates = []
        block.answer = typed.strip()
        return
    block.mode = "pick"
    block.candidates = candidates[:3]
    block.answer = choice.strip() if isinstance(choice, str) else choice
    block.preselected = block.answer


def confirm(
    reads: Reads,
    still_id: str,
    choice: str | None,
    typed: str | None,
    by: Literal["device", "reviewer"],
    field_id: Literal["meter_number", "panel_brand", "main_breaker_amps"] = "meter_number",
    cfg: ReadsConfig | None = None,
) -> Reads:
    updated = reads.model_copy(deep=True)
    block = updated.confirm or ConfirmBlock()
    if field_id in block.locks and by != "reviewer":
        if _same_answer(updated, field_id, choice, typed):
            return updated
        raise ConfirmationConflict()
    if field_id == "meter_number":
        if choice is None and typed is None:
            if updated.meter is None:
                updated.meter = MeterRead(status="unreadable")
            updated.meter.status = "unreadable"
            updated.meter.value = None
            updated.meter.core = None
            updated.meter.confidence = 0.0
            block.answer = None
        else:
            value = typed if typed is not None else choice
            if choice is not None and typed is None and choice not in _candidate_values(updated):
                raise ReadsValueError("value_not_candidate")
            if updated.meter is None:
                updated.meter = MeterRead(status="auto_fill")
            updated.meter.status = "auto_fill"
            updated.meter.value = (value or "").strip()
            updated.meter.core = core_of(updated.meter.value)
            updated.meter.confidence = CONFIDENCE["confirmed"]
            updated.meter.agree = True
            block.answer = updated.meter.value
        _stamp_confirm(block, still_id, field_id, choice, typed, by, _candidate_values(updated))
        updated.confirm = block
        if updated.meter is not None and not updated.meter.images:
            updated.meter.images = [still_id]
    else:
        _confirm_panel(updated, field_id, choice if choice is not None else typed, by)
        _stamp_confirm(block, still_id, field_id, choice, typed, by, [])
        updated.confirm = block
    updated.facts, updated.views_needed = to_criteria(updated, cfg or ReadsConfig.from_env())
    return updated


class ReadsValueError(ReadsError):
    pass


def _confirm_panel(reads: Reads, field_id: str, value: str | None, by: str) -> None:
    if reads.panel is None or reads.panel.merged is None or value is None:
        return
    panel = reads.panel.merged
    if field_id == "panel_brand":
        panel.brand = value.strip()
        panel.brand_evidence = "printed_text"
    elif field_id == "main_breaker_amps":
        amps = as_int(value)
        if amps is None:
            raise ReadsValueError("amps_not_integer")
        panel.main_breaker_amps = amps
    reads.warnings.append(f"{field_id}_confirmed_by_{by}")
