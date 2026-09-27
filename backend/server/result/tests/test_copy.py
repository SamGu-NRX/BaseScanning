"""Catalogue selection, headlines, and the customer-copy limits."""

import itertools
import json
from typing import get_args

import pytest

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
VARS = {
    "side": side_text(2.0),
    "s_ft": 12,
    "out_ft": 12,
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


def test_missing_variable_raises():
    entry = select_prompt(FALLBACK, kind="past_end")
    with pytest.raises(KeyError):
        render(entry)


def test_catalogue_round_trip(tmp_path):
    path = tmp_path / "prompts.json"
    path.write_text(json.dumps(catalogue_document(FALLBACK)))
    loaded = load_catalogue(path)
    assert loaded.source == str(path)
    assert _rendered(outcome="eligible").title == render(select_prompt(loaded, outcome="eligible"), **VARS).title
