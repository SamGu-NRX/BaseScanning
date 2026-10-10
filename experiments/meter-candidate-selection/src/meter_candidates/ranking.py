"""Rank the candidates and decide what the pipeline offers.

The score and the offer rule are hand-set from meter-closeup's design and were fixed
before the first run of this experiment; nothing here is calibrated on any outcome, and no
threshold is tuned. Departures from `locate.score` (meter-closeup), which weighs
8*barcode_confirmed + 3*keyword + 2*alone + 1*length_ok - 6*spec - 4*vertical - 6*zeros
plus a height tie-break: the height tie-break is dropped because the two arms report
heights on different pixel scales (Tesseract pixels vs hand-authored boxes), and ties
break toward the shorter core (more specific), then lexicographically, deterministically.

The offer rule is structural, from meter-closeup's `length_ok` and the plausible pool of
`locate.rules`: 6-14 characters with at least 5 digits, not a spec line, not vertical, not
all zeros. An empty offerable list is the pipeline's rejection.
"""

LENGTH_MIN = 6
LENGTH_MAX = 14
MIN_DIGITS = 5


def plausible(c: dict) -> bool:
    return (
        not c["spec"]
        and not c["vertical"]
        and not c["zeros"]
        and LENGTH_MIN <= len(c["core"]) <= LENGTH_MAX
        and sum(ch.isdigit() for ch in c["core"]) >= MIN_DIGITS
    )


def score(c: dict) -> float:
    return (
        8 * c["barcode_confirmed"]
        + 3 * c["keyword"]
        + 2 * c["alone"]
        + 1 * c["length_ok"]
        - 6 * c["spec"]
        - 4 * c["vertical"]
        - 6 * c["zeros"]
    )


def offers(found: list[dict]) -> list[dict]:
    """Plausible candidates, best first."""
    pool = [c for c in found if plausible(c)]
    return sorted(pool, key=lambda c: (-score(c), len(c["core"]), c["core"]))


def ranked(found: list[dict]) -> list[str]:
    """Offered cores, best first, each core once."""
    return list(dict.fromkeys(c["core"] for c in offers(found)))
