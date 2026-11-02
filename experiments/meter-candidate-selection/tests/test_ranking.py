"""Ranking: fixed hand-set weights, structural offer rule, deterministic order."""

from meter_candidates.candidates import candidates
from meter_candidates.ranking import offers, plausible, ranked, score


def _candidate(core_text, **overrides):
    base = {
        "core": core_text,
        "box": [0, 0, 100, 20],
        "barcode_confirmed": False,
        "keyword": False,
        "alone": False,
        "spec": False,
        "vertical": False,
        "zeros": False,
        "length_ok": False,
        "height": 20,
    }
    base.update(overrides)
    return base


def test_plausible_is_the_structural_offer_rule():
    assert plausible(_candidate("24681097"))
    # The rule reads the core's shape, not the feature flags: length_ok scores, it
    # does not gate, so a well-formed core stays plausible with every flag off.
    for bad in ("24681", "246810970000000", "12AB34CD"):  # short, 15 chars, 4 digits
        assert not plausible(_candidate(bad))
    for field in ("spec", "vertical", "zeros"):
        assert not plausible(_candidate("24681097", **{field: True}))


def test_confirmed_beats_keyword_beats_plain():
    plain = _candidate("24681097")
    keyword = _candidate("24681097", keyword=True, alone=True, length_ok=True)
    confirmed = _candidate("24681097", barcode_confirmed=True)
    assert score(confirmed) > score(keyword) > score(plain)


def test_ties_break_toward_the_shorter_core_then_lexicographically():
    a = _candidate("24681357", keyword=True, alone=True, length_ok=True)
    b = _candidate("13572468", keyword=True, alone=True, length_ok=True)
    c = _candidate("1357246", keyword=True, alone=True, length_ok=True)
    assert ranked([a, b, c]) == ["1357246", "13572468", "24681357"]


def test_rejects_when_nothing_is_plausible():
    found = candidates(
        {
            "lines": [
                {"text": "20(60)A 50Hz", "box": [0, 0, 190, 20]},
                {"text": "CL 200", "box": [0, 40, 96, 20]},
            ],
            "barcodes": [],
        }
    )
    assert found and not offers(found) and ranked(found) == []
