"""`make sim-app` passes only a run that walked the capture flow to its result."""

import argparse
import re
from pathlib import Path

import pytest

from hsverify.simrun import ShotRecord, out_of_order, state_pair, unanswered


def shot(state: str) -> ShotRecord:
    return ShotRecord(1, state, 0.0, "01.png", False, f"STATE={state}")


def sim_app_problems(sequence: str) -> list[str]:
    """What `make sim-app`'s state requirements say about a run that logged `sequence`."""
    makefile = (Path(__file__).resolve().parents[1] / "Makefile").read_text()
    recipe = makefile.split("\nsim-app:\n", 1)[1].split("\n\n", 1)[0]
    orders = re.findall(r"--require-order (\S+)", recipe)
    pairs = re.findall(r"--require-then (\S+)", recipe)
    assert orders and pairs, recipe
    states = [shot(name) for name in sequence.split(">")]
    problems = [p for order in orders for p in out_of_order(order.split(","), states)]
    closing = frozenset(order.split(",")[-1] for order in orders)
    return problems + unanswered([state_pair(p) for p in pairs], states, closing)


# State sequences of recorded `make sim-app` runs on t3/ios-mvf (report.json in
# ~/house-scanning-data/reports/sim/): one upload at a39d0a5, a recapture round at f14947e, and
# recapture plus spot checks at c820cf1.
RECORDED_RUNS = [
    "onboarding>findMeter>meterCloseUp>wallWalk>markFeatures>gapRequest>uploading>result>resultAR"
    ">result",
    "onboarding>findMeter>meterCloseUp>wallWalk>markFeatures>gapRequest>uploading>gapRequest"
    ">uploading>result>resultAR>result",
    "onboarding>findMeter>meterCloseUp>wallWalk>markFeatures>gapRequest>uploading>gapRequest"
    ">uploading>spotConfirm>uploading>spotConfirm>uploading>spotConfirm>result>resultAR>result",
]


@pytest.mark.parametrize("sequence", RECORDED_RUNS)
def test_the_recorded_flows_pass(sequence):
    assert sim_app_problems(sequence) == []


@pytest.mark.parametrize(
    ("sequence", "problem"),
    [
        # A cached or prebuilt scan shown straight away: no capture, no upload.
        ("onboarding>result>resultAR>result", "no findMeter at all"),
        # Captured, then a result with no upload behind it.
        ("onboarding>findMeter>meterCloseUp>wallWalk>markFeatures>result", "no uploading after"),
        # Uploaded before the walk that should have produced the scan.
        (
            "onboarding>uploading>findMeter>meterCloseUp>wallWalk>markFeatures>result",
            "no uploading after markFeatures",
        ),
        # The answer asked for more, the app showed the request, then the result without sending.
        (
            "onboarding>findMeter>meterCloseUp>wallWalk>markFeatures>uploading>gapRequest>result",
            "state 7 gapRequest was not followed by uploading before result",
        ),
        # The request's upload came only after the app had already shown a result.
        (
            "onboarding>findMeter>meterCloseUp>wallWalk>markFeatures>uploading>gapRequest>result"
            ">uploading>result",
            "state 7 gapRequest was not followed by uploading before result",
        ),
        # Two requests, one upload: the first request was never sent on its own.
        (
            "onboarding>findMeter>meterCloseUp>wallWalk>markFeatures>gapRequest>gapRequest"
            ">uploading>result",
            "state 6 gapRequest was not followed by uploading before gapRequest",
        ),
        # The request at the end of the run was never answered at all.
        (
            "onboarding>findMeter>meterCloseUp>wallWalk>markFeatures>uploading>result>gapRequest",
            "state 8 gapRequest was never followed by uploading",
        ),
    ],
)
def test_a_shortcut_to_the_result_fails(sequence, problem):
    problems = sim_app_problems(sequence)
    assert any(problem in p for p in problems), problems


def test_a_malformed_pair_is_refused():
    with pytest.raises(argparse.ArgumentTypeError, match="FIRST:THEN"):
        state_pair("gapRequest")
