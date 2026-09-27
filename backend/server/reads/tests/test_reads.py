"""Policy, worker, and fact tests. Identifier strings are zero-filled stand-ins, never real meter numbers."""

from __future__ import annotations

import io
import logging
import socket

import pytest
from PIL import Image

from server.contracts.jobs import StageContext, StageResult
from server.reads import run
from server.reads.aggregate import build_reads, merge_meter, merge_panels
from server.reads.barcode import read_barcodes
from server.reads.cache import MemoryStorage, cache_key, get_json, identity_key, put_json, still_key
from server.reads.candidates import core, normalize
from server.reads.config import ReadsConfig
from server.reads.facts import coverage_contract_errors, to_criteria
from server.reads.hints import hints_for
from server.reads.panel_policy import decide_panel, needs_escalation, should_escalate
from server.reads.policy import CONFIDENCE, confirm, decide_meter
from server.reads.types import (
    Candidate,
    ConfirmationConflict,
    MeterRead,
    PanelBundle,
    PanelRead,
    ReaderAnswer,
    Reads,
    StillRef,
    StillResult,
)
from server.reads.worker import read_still

A = "000000001"
B = "000000002"
C = "000000003"
ZERO = "000000000"


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (20, 40, 60)).save(buf, format="JPEG")
    return buf.getvalue()


def _reader(model: str, payload: dict | None, error: str | None = None, prompt: str = "meter/v0") -> ReaderAnswer:
    return ReaderAnswer(model=model, prompt_id=prompt, answer=payload, error=error)


def _meter(number: str | None, extra: list[str] | None = None, **kwargs: object) -> dict:
    body = {
        "meter_number": number,
        "number_candidates": extra or [],
        "class_label": "CL200",
        "legibility": "good",
        "manufacturer": "Acme",
        "form": "FM2S",
    }
    body.update(kwargs)
    return body


def _cfg(**kwargs: object) -> ReadsConfig:
    return ReadsConfig(**kwargs)  # type: ignore[arg-type]


def _still(purpose: str = "meter_close", still_id: str = "s1") -> StillRef:
    return StillRef(
        capture_id="cap_test",
        still_id=still_id,
        path="uploads/cap_test/s1.jpg",
        sha256="a" * 64,
        purpose=purpose,
    )


def _store_image(storage: MemoryStorage, still: StillRef) -> None:
    storage.put_bytes(still.path, _jpeg(), "image/jpeg")


def test_normalize_and_core() -> None:
    assert normalize("no. 12-34") == "NO1234"
    assert core("NO. 000000001") == A
    assert core("ABC000000001") == A


def test_missing_zxing_is_an_error_outside_tests(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("server.reads.barcode.zxingcpp", None)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    image = Image.new("RGB", (8, 8))
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="zxingcpp is not installed"):
        read_barcodes(image)
    assert any("zxingcpp is not installed" in record.message for record in caplog.records)


def test_missing_zxing_skips_under_pytest(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr("server.reads.barcode.zxingcpp", None)
    image = Image.new("RGB", (8, 8))
    with caplog.at_level(logging.ERROR):
        assert read_barcodes(image) == []
    assert any("zxingcpp is not installed" in record.message for record in caplog.records)


def test_auto_fill_requires_barcode() -> None:
    gpt = _reader("gpt-5.5", _meter(A))
    grok = _reader("grok-4.7", _meter(A))
    read = decide_meter(gpt, grok, [{"payload": A}], _cfg())
    assert read.status == "auto_fill"
    assert read.barcode_match is True
    assert read.confidence == CONFIDENCE["auto_fill"]
    assert read.core == A


def test_agreement_without_barcode_is_prefill() -> None:
    read = decide_meter(_reader("gpt-5.5", _meter(A)), _reader("grok-4.7", _meter(A)), [], _cfg())
    assert read.status == "prefill"
    assert read.barcode_match is False
    assert read.confidence == CONFIDENCE["prefill_agree"]


def test_autofill_flag_promotes_agreement() -> None:
    read = decide_meter(
        _reader("gpt-5.5", _meter(A)),
        _reader("grok-4.7", _meter(A)),
        [],
        _cfg(autofill_without_barcode=True),
    )
    assert read.status == "auto_fill"


def test_one_barcode_match_is_prefill() -> None:
    read = decide_meter(
        _reader("gpt-5.5", _meter(A)),
        _reader("grok-4.7", _meter(B)),
        [{"payload": A}],
        _cfg(),
    )
    assert read.status == "prefill"
    assert read.core == A
    assert read.confidence == CONFIDENCE["prefill_barcode"]


def test_disagreement_is_choose_trimmed_to_three() -> None:
    gpt = _reader("gpt-5.5", _meter(A, [C, "000000004", "000000005"]))
    grok = _reader("grok-4.7", _meter(B, ["000000006"]))
    read = decide_meter(gpt, grok, [], _cfg())
    assert read.status == "choose"
    assert read.value is None
    assert len(read.candidates) == 3
    assert [item.core for item in read.candidates[:2]] == [A, B]


def test_implausible_is_demoted_not_dropped() -> None:
    gpt = _reader("gpt-5.5", _meter("100 V 000000001"))
    grok = _reader("grok-4.7", _meter(B))
    read = decide_meter(gpt, grok, [], _cfg())
    assert read.status == "choose"
    flags = {item.core: item.flags for item in read.candidates}
    assert any(flags.values())
    assert read.candidates[-1].flags == ["implausible_length"]


def test_all_zero_core_is_implausible() -> None:
    read = decide_meter(_reader("gpt-5.5", _meter(ZERO)), _reader("grok-4.7", _meter(A)), [], _cfg())
    zero = next(item for item in read.candidates if item.core == ZERO)
    assert "implausible_length" in zero.flags
    assert zero in read.candidates


def test_gpt_timeout_is_single_reader_prefill() -> None:
    read = decide_meter(
        _reader("gpt-5.5", _meter(A, [B, C])),
        _reader("grok-4.7", None, error="vlm_timeout"),
        [],
        _cfg(),
    )
    assert read.status == "single_reader"
    assert read.core == A
    assert read.confidence == CONFIDENCE["gpt-5.5"]
    assert len(read.candidates) == 3


def test_grok_only_confidence() -> None:
    read = decide_meter(
        _reader("gpt-5.5", None, error="vlm_timeout"),
        _reader("grok-4.7", _meter(B)),
        [],
        _cfg(),
    )
    assert read.status == "single_reader"
    assert read.confidence == CONFIDENCE["grok-4.7"]


def test_unreadable_hint_is_type_mode() -> None:
    still = _still()
    meter = MeterRead(status="unreadable")
    hints = hints_for(StillResult(still=still, meter=meter))
    assert [item.code for item in hints] == ["meter_plate_unreadable", "meter_number_confirm"]
    confirm_hint = hints[1].confirm
    assert confirm_hint is not None
    assert confirm_hint.mode == "type"
    assert confirm_hint.candidates == []
    assert confirm_hint.allowNone is True
    assert confirm_hint.preselected is None


def test_autopass_forces_escalation() -> None:
    assert should_escalate({"legibility": "poor"}, force=True) is True
    assert needs_escalation({"legibility": "poor"}) is False


def test_both_null_is_unreadable() -> None:
    read = decide_meter(_reader("gpt-5.5", _meter(None)), _reader("grok-4.7", _meter(None)), [], _cfg())
    assert read.status == "unreadable"


def test_both_disabled() -> None:
    read = decide_meter(
        _reader("gpt-5.5", None, error="disabled"),
        _reader("grok-4.7", None, error="disabled"),
        [],
        _cfg(external_vision=False),
    )
    assert read.status == "disabled"


def test_cl320_agreement_and_other_class() -> None:
    both = decide_meter(
        _reader("gpt-5.5", _meter(A, class_label="CL320")),
        _reader("grok-4.7", _meter(A, class_label="CL 320")),
        [{"payload": A}],
        _cfg(),
    )
    assert both.cl320 == "cl320_meter"
    other = decide_meter(
        _reader("gpt-5.5", _meter(A, class_label="CL200")),
        _reader("grok-4.7", _meter(A, class_label="CL200")),
        [{"payload": A}],
        _cfg(),
    )
    assert other.cl320 == "not_cl320_meter"
    rating = decide_meter(
        _reader("gpt-5.5", _meter(A, class_label="30(200)A")),
        _reader("grok-4.7", _meter(A, class_label="30(200)A")),
        [{"payload": A}],
        _cfg(),
    )
    assert rating.cl320 == "not_cl320_meter"


def test_oblique_corroboration_and_fallback() -> None:
    close = StillResult(
        still=_still("meter_close", "s1"),
        meter=decide_meter(_reader("gpt-5.5", _meter(A)), _reader("grok-4.7", _meter(A)), [], _cfg()),
    )
    oblique = StillResult(
        still=_still("meter_oblique", "s2"),
        meter=decide_meter(_reader("gpt-5.5", _meter(A)), _reader("grok-4.7", _meter(A)), [], _cfg()),
    )
    merged, warnings, median = merge_meter([close, oblique])
    assert merged is not None
    assert median == 2
    assert merged.images == ["s1", "s2"]
    assert "value_from_oblique" not in warnings
    blank = close.model_copy(deep=True)
    assert blank.meter is not None
    blank.meter.status = "unreadable"
    blank.meter.core = None
    used, warnings, _median = merge_meter([blank, oblique])
    assert used is not None and used.core == A
    assert "value_from_oblique" in warnings


def test_later_weak_close_keeps_better_still() -> None:
    legible = StillResult(
        still=_still("meter_close", "s1"),
        meter=MeterRead(status="prefill", value=A, core=A, agree=True),
    )
    skipped = StillResult(still=_still("meter_close", "s2"), meter=MeterRead(status="disabled"))
    kept, warnings, _median = merge_meter([legible, skipped])
    assert kept is not None and kept.status == "prefill" and kept.value == A
    assert kept.images == ["s1"]
    assert "value_from_oblique" not in warnings
    oblique = StillResult(
        still=_still("meter_oblique", "s3"),
        meter=MeterRead(status="prefill", value=B, core=B, agree=True),
    )
    from_oblique, warnings, _median = merge_meter([skipped, oblique])
    assert from_oblique is not None and from_oblique.core == B and from_oblique.images == ["s3"]
    assert "value_from_oblique" in warnings
    filled = StillResult(
        still=_still("meter_close", "s4"),
        meter=MeterRead(status="auto_fill", value=C, core=C, agree=True),
    )
    replaced, _warnings, _median = merge_meter([legible, filled])
    assert replaced is not None and replaced.value == C and replaced.images == ["s4"]


def test_merge_panels_brand_and_amps_conflict() -> None:
    def panel(still_id: str, brand: str, amps: int) -> StillResult:
        return StillResult(
            still=_still("panel_open", still_id),
            panel=PanelRead(
                brand=brand,
                brand_evidence="printed_text",
                main_breaker_amps=amps,
                hazard="none",
                legibility="good",
            ),
        )

    merged, per_image, _count, warnings = merge_panels([panel("s3", "eaton", 200), panel("s4", "siemens", 150)])
    assert merged is not None and merged.brand is None
    assert merged.main_breaker_amps is None
    assert "brand_conflict" in warnings
    assert "amps_conflict" in warnings
    assert set(per_image) == {"s3", "s4"}


def test_merge_panels_confirmed_wins() -> None:
    quiet = StillResult(still=_still("panel_close", "s3"), panel=PanelRead(hazard="none", brand="eaton"))
    loud = StillResult(
        still=_still("panel_open", "s4"),
        panel=PanelRead(hazard="confirmed", hazard_line="federal_pacific_stab_lok"),
    )
    merged, _per, _count, _warnings = merge_panels([quiet, loud])
    assert merged is not None and merged.hazard == "confirmed"


def test_escalation_triggers() -> None:
    assert needs_escalation({"hazard_line": "challenger", "legibility": "poor"})
    assert needs_escalation({"manufacturer": "Eaton", "brand_evidence": "logo_badge", "legibility": "poor"})
    assert needs_escalation({"main_breaker_amps": 200, "legibility": "poor"})
    assert needs_escalation({"panel_rating_amps": 200, "legibility": "poor"})
    assert needs_escalation({"legibility": "good"})
    assert not needs_escalation({"legibility": "poor", "hazard_line": "none_identified", "brand_evidence": "none"})


def test_both_flag_hazard_confirmed() -> None:
    mini = _reader("gpt-5.4-mini", {"hazard_line": "federal_pacific_stab_lok", "legibility": "good"}, prompt="panel/v0")
    gpt = _reader("gpt-5.5", {"hazard_line": "federal_pacific_stab_lok", "legibility": "good"}, prompt="panel/v0")
    panel = decide_panel(mini, gpt)
    assert panel.hazard == "confirmed"


def test_one_flag_is_disputed() -> None:
    mini = _reader("gpt-5.4-mini", {"hazard_line": "none_identified"}, prompt="panel/v0")
    gpt = _reader("gpt-5.5", {"hazard_line": "challenger"}, prompt="panel/v0")
    assert decide_panel(mini, gpt).hazard == "disputed"


def test_fail_safe_single_reader_hazard() -> None:
    gpt = _reader("gpt-5.5", {"hazard_line": "zinsco_sylvania_magnetrip"}, prompt="panel/v0")
    mini = _reader("gpt-5.4-mini", None, error="vlm_timeout", prompt="panel/v0")
    assert decide_panel(mini, gpt).hazard == "confirmed"


def test_pushmatic_is_not_confirmed() -> None:
    mini = _reader("gpt-5.4-mini", {"hazard_line": "pushmatic", "manufacturer": "Pushmatic"}, prompt="panel/v0")
    gpt = _reader("gpt-5.5", {"hazard_line": "pushmatic", "manufacturer": "Pushmatic"}, prompt="panel/v0")
    panel = decide_panel(mini, gpt)
    assert panel.hazard != "confirmed"
    assert "pushmatic" in panel.review_hints


def _reads_with(meter: MeterRead) -> Reads:
    meter.images = meter.images or ["s1"]
    reads = Reads(capture_id="cap_test", meter=meter, confirm=None)
    reads.facts, reads.views_needed = to_criteria(reads)
    return reads


def test_confirm_once_and_device_conflict() -> None:
    meter = decide_meter(_reader("gpt-5.5", _meter(A)), _reader("grok-4.7", _meter(B)), [], _cfg())
    reads = _reads_with(meter)
    chosen = reads.meter.candidates[0].value if reads.meter else A
    updated = confirm(reads, "s1", chosen, None, "device")
    assert updated.meter is not None and updated.meter.status == "auto_fill"
    assert updated.meter.confidence == 1.0
    assert updated.facts[0]["fact"]["raw"] == "confirmed:device"
    assert updated.confirm is not None
    assert updated.confirm.mode == "pick"
    assert updated.confirm.preselected == chosen
    assert updated.confirm.imageId == "s1"
    assert updated.confirm.fieldId == "meter_number"
    assert updated.confirm.allowNone is True
    again = confirm(updated, "s1", chosen, None, "device")
    assert again.meter is not None and again.meter.value == chosen
    with pytest.raises(ConfirmationConflict):
        confirm(updated, "s1", B, None, "device")


def test_later_panel_confirm_keeps_meter_actor() -> None:
    meter = decide_meter(_reader("gpt-5.5", _meter(A)), _reader("grok-4.7", _meter(B)), [], _cfg())
    reads = _reads_with(meter)
    chosen = reads.meter.candidates[0].value if reads.meter else A
    once = confirm(reads, "s1", chosen, None, "device")
    once.panel = PanelBundle(
        merged=PanelRead(brand="unknown"),
        per_image={"s3": PanelRead(brand="unknown")},
    )
    twice = confirm(once, "s3", "eaton", None, "reviewer", "panel_brand")
    assert twice.confirm is not None
    assert twice.confirm.fieldId == "panel_brand"
    assert twice.confirm.mode == "pick"
    assert twice.confirm.answer == "eaton"
    assert twice.confirm.preselected == "eaton"
    assert twice.confirm.answered_by == "reviewer"
    assert twice.confirm.locks["meter_number"] == "device"
    meter_item = next(item for item in twice.facts if item["id"] == "meter_number")
    assert meter_item["fact"]["raw"] == "confirmed:device"


def test_reviewer_can_override() -> None:
    meter = decide_meter(_reader("gpt-5.5", _meter(A)), _reader("grok-4.7", _meter(A)), [], _cfg())
    reads = _reads_with(meter)
    once = confirm(reads, "s1", A, None, "device")
    twice = confirm(once, "s1", None, B, "reviewer")
    assert twice.meter is not None and twice.meter.value == B
    assert twice.confirm is not None and twice.confirm.mode == "type"
    assert twice.confirm.preselected is None


def test_confirm_none_is_unreadable() -> None:
    meter = decide_meter(_reader("gpt-5.5", _meter(A)), _reader("grok-4.7", _meter(B)), [], _cfg())
    updated = confirm(_reads_with(meter), "s1", None, None, "device")
    assert updated.meter is not None and updated.meter.status == "unreadable"
    assert updated.confirm is not None and updated.confirm.mode == "none"
    assert updated.confirm.preselected is None
    assert updated.confirm.candidates == []


def test_facts_for_each_meter_status() -> None:
    auto = _reads_with(
        decide_meter(_reader("gpt-5.5", _meter(A)), _reader("grok-4.7", _meter(A)), [{"payload": A}], _cfg())
    )
    prefill = _reads_with(decide_meter(_reader("gpt-5.5", _meter(A)), _reader("grok-4.7", _meter(A)), [], _cfg()))
    unread = _reads_with(decide_meter(_reader("gpt-5.5", _meter(None)), _reader("grok-4.7", _meter(None)), [], _cfg()))
    assert auto.facts[0]["outcome"] == "pass" and auto.facts[0]["fact"]["clean"] is True
    assert prefill.facts[0]["outcome"] == "unsure"
    assert prefill.facts[0]["unsure_cause"] == "unknown_attribute"
    assert unread.facts[0]["unsure_cause"] == "unobserved"
    assert all("prompt" not in view for view in prefill.views_needed)


def test_pass_fact_has_no_contract_error() -> None:
    reads = _reads_with(
        decide_meter(_reader("gpt-5.5", _meter(A)), _reader("grok-4.7", _meter(A)), [{"payload": A}], _cfg())
    )
    verdict = _wrap(reads.facts, reads.views_needed)
    assert coverage_contract_errors(verdict) == []


def test_panel_fact_hazard_and_clean_brand() -> None:
    hazard = Reads(
        capture_id="cap_test",
        panel=PanelBundle(
            merged=PanelRead(hazard="confirmed", hazard_line="challenger", brand="challenger", legibility="good"),
            per_image={"s3": PanelRead(hazard="confirmed", hazard_line="challenger")},
        ),
    )
    items, _views = to_criteria(hazard)
    assert items[0]["id"] == "panel_recall"
    assert items[0]["unsure_cause"] == "rule_requires_review"
    clean = Reads(
        capture_id="cap_test",
        panel=PanelBundle(
            merged=PanelRead(hazard="none", brand="eaton", brand_evidence="printed_text", legibility="good"),
            per_image={"s3": PanelRead(brand="eaton")},
        ),
    )
    items, _views = to_criteria(clean)
    assert items[0]["fact"]["value"] == "none_identified"
    assert items[0]["outcome"] == "unsure"
    held, _views = to_criteria(clean, _cfg(panel_autopass=True))
    assert held[0]["outcome"] == "unsure"
    agreed = Reads(
        capture_id="cap_test",
        panel=PanelBundle(
            merged=PanelRead(
                hazard="none",
                brand="eaton",
                brand_evidence="printed_text",
                legibility="good",
                gpt_read=True,
                brand_mini="eaton",
                brand_gpt="eaton",
                evidence_mini="printed_text",
                evidence_gpt="printed_text",
                hazard_line_mini="none_identified",
                hazard_line_gpt="none_identified",
            ),
            per_image={"s3": PanelRead(brand="eaton")},
        ),
    )
    passed, _views = to_criteria(agreed, _cfg(panel_autopass=True))
    assert passed[0]["outcome"] == "pass"
    assert "hazard_line_gpt" in passed[0]["fact"]["raw"]


def _wrap(criteria: list[dict], views: list[dict]) -> dict:
    return {
        "format": "base-verdict/v0",
        "verdict_id": "v_test",
        "scene": {"scene_id": "sc", "site": "t"},
        "evaluator": {"path": "reads"},
        "site": {"decision": "manual_review", "reasons": []},
        "criteria": criteria,
        "views_needed": views,
    }


def test_worker_disabled_opens_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("socket opened")

    monkeypatch.setattr(socket, "socket", boom)
    storage = MemoryStorage()
    still = _still()
    _store_image(storage, still)
    result, hints = read_still(still, storage, _cfg(external_vision=False))
    assert result.meter is not None and result.meter.status == "disabled"
    assert hints == []
    identity = get_json(storage, identity_key(still.capture_id))
    assert identity["images"] == [still.still_id]
    reads = build_reads("cap_test", storage, _cfg(external_vision=False))
    assert reads.status == "disabled"
    assert reads.facts
    assert all(item["unsure_cause"] == "not_evaluated" for item in reads.facts)


def test_cache_hit_costs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> ReaderAnswer:
        raise AssertionError("reader called")

    monkeypatch.setattr("server.reads.worker.call_reader", boom)
    storage = MemoryStorage()
    still = _still("meter_oblique")
    _store_image(storage, still)
    cfg = _cfg()
    for model in ("gpt-5.5", "grok-4.7"):
        put_json(
            storage,
            cache_key(still.capture_id, still.sha256, model, "meter/v0", cfg.max_side_px),
            _reader(model, _meter(A)).model_dump(mode="json"),
        )
    result, _hints = read_still(still, storage, cfg)
    assert result.cost_usd == 0
    assert result.meter is not None and result.meter.status == "prefill"
    assert all(item.cached for item in result.answers)


def test_budget_stops_new_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> ReaderAnswer:
        raise AssertionError("reader called")

    monkeypatch.setattr("server.reads.worker.call_reader", boom)
    storage = MemoryStorage()
    prior = StillResult(
        still=_still("meter_close", "s0"),
        answers=[_reader("gpt-5.5", _meter(A))],
        meter=MeterRead(status="prefill", value=A, core=A, images=["s0"]),
        cost_usd=1.0,
    )
    put_json(storage, still_key("cap_test", "s0"), prior.model_dump(mode="json"))
    still = _still("meter_oblique", "s2")
    still = still.model_copy(update={"path": "uploads/cap_test/s2.jpg"})
    _store_image(storage, still)
    result, _hints = read_still(still, storage, _cfg(budget_usd_per_house=0.40))
    assert all(item.error == "not_evaluated" for item in result.answers)
    reads = build_reads("cap_test", storage, _cfg(budget_usd_per_house=0.40))
    assert "budget_exhausted" in reads.warnings


def test_read_still_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake(*_args: object, **_kwargs: object) -> ReaderAnswer:
        calls["n"] += 1
        model = _args[0]
        return _reader(str(model), _meter(A))

    monkeypatch.setattr("server.reads.worker.call_reader", fake)
    storage = MemoryStorage()
    still = _still("meter_oblique")
    _store_image(storage, still)
    first, _hints = read_still(still, storage, _cfg())
    second, _hints = read_still(still, storage, _cfg())
    assert first.meter is not None and second.meter is not None
    assert second.meter.status == first.meter.status
    assert calls["n"] == 2


def test_unknown_purpose() -> None:
    storage = MemoryStorage()
    still = _still("panel_surround")
    with pytest.raises(Exception, match="purpose_not_readable"):
        read_still(still, storage, _cfg())


def _stored_meter(status: str, value: str | None, candidates: list[str]) -> MeterRead:
    return MeterRead(
        status=status,  # type: ignore[arg-type]
        value=value,
        core=value,
        images=["s1"],
        candidates=[
            Candidate(value=item, core=item, sources=["gpt-5.5"], rank=index) for index, item in enumerate(candidates)
        ],
    )


def test_reads_json_confirm_matches_hint_modes() -> None:
    storage = MemoryStorage()
    prefill = StillResult(still=_still("meter_close", "s1"), meter=_stored_meter("prefill", A, [A]))
    put_json(storage, still_key("cap_test", "s1"), prefill.model_dump(mode="json"))
    picked = build_reads("cap_test", storage, _cfg(external_vision=False))
    assert picked.confirm is not None
    assert picked.confirm.mode == "pick"
    assert picked.confirm.preselected == A
    assert picked.confirm.candidates == [A]
    assert picked.confirm.imageId == "s1"
    assert picked.confirm.allowNone is True

    choose_storage = MemoryStorage()
    choose = StillResult(still=_still("meter_close", "s1"), meter=_stored_meter("choose", None, [A, B]))
    put_json(choose_storage, still_key("cap_test", "s1"), choose.model_dump(mode="json"))
    offered = build_reads("cap_test", choose_storage, _cfg(external_vision=False))
    assert offered.confirm is not None
    assert offered.confirm.mode == "pick"
    assert offered.confirm.preselected is None
    assert offered.confirm.candidates == [A, B]

    unread_storage = MemoryStorage()
    unread = StillResult(still=_still("meter_close", "s1"), meter=_stored_meter("unreadable", None, []))
    put_json(unread_storage, still_key("cap_test", "s1"), unread.model_dump(mode="json"))
    typed = build_reads("cap_test", unread_storage, _cfg(external_vision=False))
    assert typed.confirm is not None
    assert typed.confirm.mode == "type"
    assert typed.confirm.candidates == []
    assert typed.confirm.preselected is None


def test_run_returns_contract_stage_result(tmp_path) -> None:
    ctx = StageContext(
        capture_id="cap_test",
        run_id="run_test",
        stage="reads",
        attempt=1,
        branch="image",
        flow="photo",
        tier="photo_only",
        work_dir=tmp_path,
        inputs={},
        deadline_at="2026-09-26T00:00:00Z",
        heartbeat=lambda _substep: None,
        vlm_budget_usd=0.40,
        storage=MemoryStorage(),
        state=None,
        settings=type("Settings", (), {"VISION_EXTERNAL_CALLS": False})(),
    )
    result = run(ctx)
    assert isinstance(result, StageResult)
    assert result.artifacts["reads/reads.json"] == "captures/cap_test/runs/run_test/reads/reads.json"
    assert result.metrics["cost_usd"] == 0
    assert result.warnings == []


def test_merge_keeps_printed_brand_evidence() -> None:
    poor = StillResult(
        still=_still("panel_close", "s3"),
        panel=PanelRead(brand_evidence="none", legibility="poor", hazard="none"),
    )
    printed = StillResult(
        still=_still("panel_open", "s4"),
        panel=PanelRead(
            brand="eaton",
            brand_evidence="printed_text",
            legibility="good",
            hazard="none",
            brand_mini="eaton",
            brand_gpt="eaton",
            evidence_mini="printed_text",
            evidence_gpt="printed_text",
            gpt_read=True,
        ),
    )
    merged, _per_image, _count, warnings = merge_panels([poor, printed])
    assert merged is not None
    assert merged.brand == "eaton"
    assert merged.brand_evidence == "printed_text"
    assert merged.legibility == "good"
    assert merged.gpt_read is True
    assert "brand_conflict" not in warnings
    items, _views = to_criteria(
        Reads(capture_id="cap_test", panel=PanelBundle(merged=merged, per_image={"s4": printed.panel})),
    )
    assert items[0]["fact"]["value"] == "none_identified"
    assert items[0]["unsure_cause"] == "rule_requires_review"


def test_printed_brand_keeps_confirmed_hazard_lines() -> None:
    hazard = StillResult(
        still=_still("panel_close", "s3"),
        panel=PanelRead(
            hazard="confirmed",
            hazard_line="challenger",
            hazard_line_mini="challenger",
            hazard_line_gpt="challenger",
            brand="challenger",
            brand_evidence="visual",
            gpt_read=True,
        ),
    )
    printed = StillResult(
        still=_still("panel_open", "s4"),
        panel=PanelRead(
            hazard="none",
            brand="eaton",
            brand_evidence="printed_text",
            legibility="good",
            hazard_line_mini="none_identified",
            hazard_line_gpt="none_identified",
            gpt_read=True,
        ),
    )
    merged, _per_image, _count, _warnings = merge_panels([printed, hazard])
    assert merged is not None
    assert merged.hazard == "confirmed"
    assert merged.hazard_line == "challenger"
    assert merged.hazard_line_mini == "challenger"
    assert merged.hazard_line_gpt == "challenger"
    assert merged.brand == "eaton"
    assert merged.brand_evidence == "printed_text"


def test_confirm_amps_strips_suffix() -> None:
    reads = Reads(
        capture_id="cap_test",
        panel=PanelBundle(merged=PanelRead(brand="eaton", main_breaker_amps=100), per_image={"s3": PanelRead()}),
    )
    updated = confirm(reads, "s3", None, "200A", "device", "main_breaker_amps")
    assert updated.panel is not None and updated.panel.merged is not None
    assert updated.panel.merged.main_breaker_amps == 200
    again = confirm(updated, "s3", None, "200A", "device", "main_breaker_amps")
    assert again.panel is not None and again.panel.merged is not None
    assert again.panel.merged.main_breaker_amps == 200


def test_confirm_keeps_panel_autopass() -> None:
    panel = PanelRead(
        hazard="none",
        brand="eaton",
        brand_evidence="printed_text",
        legibility="good",
        gpt_read=True,
        brand_mini="eaton",
        brand_gpt="eaton",
        evidence_mini="printed_text",
        evidence_gpt="printed_text",
        hazard_line_mini="none_identified",
        hazard_line_gpt="none_identified",
    )
    meter = MeterRead(
        status="prefill",
        value=A,
        core=A,
        images=["s1"],
        candidates=[Candidate(value=A, core=A, sources=["gpt-5.5"], rank=1)],
    )
    reads = Reads(capture_id="cap_test", meter=meter, panel=PanelBundle(merged=panel, per_image={"s3": panel}))
    cfg = _cfg(panel_autopass=True)
    reads.facts, reads.views_needed = to_criteria(reads, cfg)
    updated = confirm(reads, "s1", A, None, "device", cfg=cfg)
    panel_item = next(item for item in updated.facts if item["id"] == "panel_recall")
    assert panel_item["outcome"] == "pass"


def test_budget_skip_is_not_a_retake() -> None:
    read = decide_meter(
        _reader("gpt-5.5", None, error="not_evaluated"),
        _reader("grok-4.7", None, error="not_evaluated"),
        [],
        _cfg(),
    )
    assert read.status == "disabled"
    assert hints_for(StillResult(still=_still(), meter=read)) == []
    reads = _reads_with(read)
    assert reads.facts[0]["unsure_cause"] == "not_evaluated"


def test_empty_close_keeps_meter_identity() -> None:
    storage = MemoryStorage()
    put_json(
        storage,
        identity_key("cap_test"),
        {"manufacturer": "Acme", "model_family": "I210", "images": ["s0"], "model_confidence": 0.5, "agree": False},
    )
    still = _still("meter_close", "s9").model_copy(update={"path": "uploads/cap_test/s9.jpg"})
    _store_image(storage, still)
    read_still(still, storage, _cfg(external_vision=False))
    identity = get_json(storage, identity_key("cap_test"))
    assert identity["model_family"] == "I210"
    assert identity["manufacturer"] == "Acme"


def test_packet_uses_img_and_file_digest() -> None:
    storage = MemoryStorage()
    path = "keyframes/k00003.jpg"
    storage.put_bytes(path, _jpeg(), "image/jpeg")
    sha = "b" * 64
    packet = {
        "stills": [{"id": "s1", "purpose": "meter_close", "img": path}],
        "keyframes": [{"id": "k1", "purpose": "meter_oblique", "img": path}],
        "files": [{"path": path, "sha256": sha, "role": "keyframe"}],
    }
    reads = build_reads("cap_test", storage, _cfg(external_vision=False), packet)
    assert storage.exists(still_key("cap_test", "s1"))
    assert storage.exists(still_key("cap_test", "k1"))
    assert reads.meter is not None and reads.meter.status == "disabled"


def test_sample_verdict_ids() -> None:
    meter = decide_meter(_reader("gpt-5.5", _meter(A)), _reader("grok-4.7", _meter(A)), [{"payload": A}], _cfg())
    reads = _reads_with(meter)
    assert [item["id"] for item in reads.facts] == ["meter_number", "cl320"]
    assert reads.facts[1]["outcome"] == "pass"
    assert reads.facts[0]["fact"]["coverage"][0]["rule"]["min_views"] == 1
