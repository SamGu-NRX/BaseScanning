"""Catalogue selection, headlines, and the customer-copy limits."""

import itertools
import json
from typing import get_args

from ios_kit.prompts import render as kit_render

from server.contracts.capture_check import CaptureCheckCode, HintCode
from server.result.copy import (
    CAUSE_RANK,
    CRITERION_IDS,
    FALLBACK,
    catalogue_document,
    load_catalogue,
    render,
    select_prompt,
    side_from_code,
    side_text,
    what_text,
    worst_cause,
)

BANNED = ("grazing", "unobserved", "s_m", "candidate", "coverage", "reject", "fail")
HEADLINES = {
    "eligible": "Your home looks eligible",
    "needs_more_photos": "Almost there",
    "manual_review": "We have what we need",
    "not_eligible": "Not a fit at this spot",
}
# Strings are already whole feet. Numbers are metres, as in ios_kit.prompts.render.
VARS = {
    "side": side_text(2.0),
    "s_ft": "12",
    "out_ft": "12",
    "what": what_text("window"),
    "threshold_ft": 20,
}


def _rendered(**select):
    return render(select_prompt(FALLBACK, **select), **VARS)


def test_worst_cause_order():
    assert worst_cause(["margin", "grazing", None, "generated_only"]) == "generated_only"
    assert list(CAUSE_RANK) == [
        "generated_only",
        "unobserved",
        "partial",
        "grazing",
        "unknown_attribute",
        "margin",
    ]


def test_side_and_what():
    assert side_text(-1.0) == "to the left of your meter"
    assert side_text(0.0) == "at your meter"
    assert side_text(1.0) == "to the right of your meter"
    assert side_from_code("wall_gap_left").startswith("to the left")
    assert what_text("garage_door") == "garage door"
    assert what_text(None) == "object"


def test_headlines_match_the_table():
    for kind, title in HEADLINES.items():
        assert _rendered(outcome=kind).title == title


def test_every_outcome_resolves():
    for kind in ("eligible", "needs_more_photos", "not_eligible", "manual_review", "failed"):
        assert _rendered(outcome=kind).promptId.startswith("outcome.")


def test_every_criterion_reason_resolves():
    ids = [_rendered(criterion=name).promptId for name in CRITERION_IDS]
    assert ids == [f"reason.{name}" for name in CRITERION_IDS]


def test_capture_checks_and_hints_resolve():
    checks = {code: _rendered(capture_check_code=code).promptId for code in get_args(CaptureCheckCode)}
    hints = {code: _rendered(hint_code=code).promptId for code in get_args(HintCode)}
    assert len(checks) == 16
    assert set(hints) >= set(checks)
    assert _rendered(member_action="retake_meter_closeups").promptId == "vn.close_up.meter"
    assert _rendered(member_action="truedepth_meter_scan").promptId == "ma.truedepth_meter_scan"


def test_kind_band_cause_combinations_resolve():
    kinds = ("band", "past_end", "close_up", "confirm_attribute", "region")
    bands = ("wall", "ground", "overhead", "facing", None)
    for kind, band, cause in itertools.product(kinds, bands, (*CAUSE_RANK, None)):
        got = _rendered(kind=kind, band=band, cause=cause)
        assert got.promptId


def test_copy_limits_and_banned_words():
    samples = [_rendered(outcome=kind) for kind in HEADLINES]
    samples += [_rendered(criterion=name) for name in CRITERION_IDS]
    samples += [_rendered(kind="band", band="wall", cause=cause) for cause in (*CAUSE_RANK, None)]
    samples += [_rendered(kind="band", band=band) for band in ("wall", "ground", "overhead", "facing")]
    samples += [_rendered(kind=kind) for kind in ("past_end", "close_up", "confirm_attribute", "region")]
    samples += [_rendered(capture_check_code=code) for code in get_args(CaptureCheckCode)]
    samples += [_rendered(hint_code=code) for code in get_args(HintCode)]
    samples += [_rendered(member_action=action) for action in ("retake_meter_closeups", "truedepth_meter_scan")]
    for item in samples:
        assert len(item.title) <= 40
        assert len(item.body) <= 140
        blob = (item.title + " " + item.body).lower()
        for word in BANNED:
            assert word not in blob


def test_missing_variable_falls_back():
    past_end = render(select_prompt(FALLBACK, kind="past_end"))
    assert "{" not in past_end.title + past_end.body
    assert past_end.body == "Walk to the end of this wall and photograph the corner and what comes after it."
    generic = next(entry for entry in FALLBACK.prompts if entry.prompt_id == "vn.generic")
    centred = render(generic, side="at your meter")
    assert centred.body == "Photograph the area at your meter, from about 10 ft back."
    assert ", 0 ft along" not in centred.body


def test_centred_meter_drops_zero_offset():
    generic = next(entry for entry in FALLBACK.prompts if entry.prompt_id == "vn.generic")
    for s_ft in ("0", 0, 0.0):
        body = render(generic, side="at your meter", s_ft=s_ft).body
        assert body == "Photograph the area at your meter, from about 10 ft back."
    ground = select_prompt(FALLBACK, kind="band", band="ground")
    offset = render(ground, side="to the right of your meter", s_ft=4.3, out_ft=3.7)
    assert "14 ft along" in offset.body
    assert "12 ft" in offset.body
    # 0.9 m is 2.95 ft: round gives 3, truncation gives 2.
    rounded = render(ground, side="to the right of your meter", s_ft=0.9, out_ft=0.9)
    assert "3 ft along" in rounded.body
    assert "3 ft out" in rounded.body
    kept = render(generic, side="to the right of your meter", s_ft="7")
    assert "7 ft along" in kept.body


def test_render_parity_with_the_ios_kit():
    samples = (
        {"s_ft": "0", "out_ft": "12"},
        {"s_ft": "7", "out_ft": "12"},
        {"s_ft": 0.0, "out_ft": 3.7},
        {"s_ft": 4.3, "out_ft": 3.7},
        {"s_ft": 0.9, "out_ft": 0.9},
    )
    base = {"side": "at your meter", "what": "window", "threshold_ft": "20"}
    for entry in FALLBACK.prompts:
        for extra in samples:
            kwargs = {**base, **extra}
            got = render(entry, **kwargs)
            kit = kit_render(entry, **kwargs)
            assert got.promptId == kit.promptId
            assert got.title == kit.title
            assert got.body == kit.body


def test_catalogue_round_trip(tmp_path):
    path = tmp_path / "prompts.json"
    path.write_text(json.dumps(catalogue_document(FALLBACK)))
    loaded = load_catalogue(path)
    assert loaded.source == str(path)
    assert _rendered(outcome="eligible").title == render(select_prompt(loaded, outcome="eligible"), **VARS).title
