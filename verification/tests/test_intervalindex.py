import random

import pytest

from hsverify.intervalindex import (
    SweepIndex,
    less_coverage_problems,
    more_error_problems,
    probe_starts,
)


class CountingMeter:
    def __init__(self, limit=None):
        self.samples = 0
        self.limit = limit

    def charge(self, n=1):
        self.samples += n
        if self.limit is not None and self.samples > self.limit:
            raise WorkLimit


class WorkLimit(Exception):
    pass


def result(*runs, decision="fail"):
    return {"decision": decision, "sweep": list(runs)}


def run(a, b, outcome="fail", wall_id="w1"):
    return {"wall_id": wall_id, "start_ft": [a, b], "outcome": outcome}


def test_thirty_thousand_runs_compare_with_linear_logarithmic_work():
    answer = result(*(run(i / 1e4, i / 1e4) for i in range(30_000)))
    meter = CountingMeter()
    assert more_error_problems(answer, answer, "more error", meter) == []
    assert 30_000 < meter.samples < 100 * 30_000


def test_first_original_run_wins_overlaps_including_eps_endpoints():
    answer = result(run(2, 4, "fail"), run(0, 6, "pass"), run(4, 4, "unsure"))
    index = SweepIndex(answer)
    for start in (2 - 1e-6, 2, 3, 4, 4 + 1e-6):
        assert index.outcome_at("w1", start) == "fail"
    assert index.outcome_at("w1", 2 - 2e-6) == "pass"
    assert index.outcome_at("w1", 4 + 2e-6) == "pass"


def test_index_agrees_with_original_linear_lookup_on_overlapping_runs():
    rng = random.Random(731)
    runs = [
        run(
            a := rng.randrange(-20, 20),
            a + rng.randrange(0, 10),
            rng.choice(["pass", "fail", "unsure"]),
        )
        for _ in range(200)
    ]
    index = SweepIndex(result(*runs))
    points = {x for r in runs for edge in r["start_ft"] for x in (edge - 1e-6, edge, edge + 1e-6)}
    points.update(rng.uniform(-30, 30) for _ in range(500))
    for start in points:
        expected = next(
            (
                r["outcome"]
                for r in runs
                if r["start_ft"][0] - 1e-6 <= start <= r["start_ft"][1] + 1e-6
            ),
            None,
        )
        assert index.outcome_at("w1", start) == expected


def test_gap_neighbours_and_equal_endpoint_ties_preserve_original_order():
    index = SweepIndex(result(run(0, 1, "fail"), run(0, 1, "pass"), run(3, 4, "unsure")))
    assert index.outcomes_near("w1", 2) == ({"fail", "unsure"}, "unswept beside fail and unsure")
    assert index.outcomes_near("w1", -1) == ({"fail"}, "past the original sweep")
    assert index.outcomes_near("w1", 5) == ({"fail"}, "past the original sweep")
    assert index.outcomes_near("missing", 0) == (set(), "no run on its wall")


def test_newly_evaluated_wall_can_fail_but_cannot_pass():
    before = result(run(0, 1))
    assert more_error_problems(before, result(run(0, 1, "fail", "w2")), "error") == []
    assert more_error_problems(before, result(run(0, 1, "pass", "w2")), "error")
    assert less_coverage_problems(before, result(run(0, 1, "pass", "w2")), "coverage")


def test_unswept_gap_can_keep_neighbour_pass_but_not_create_one():
    after = result(run(2, 2, "pass"))
    assert more_error_problems(result(run(0, 1, "pass"), run(3, 4)), after, "error") == []
    assert less_coverage_problems(result(run(0, 1, "pass"), run(3, 4)), after, "coverage") == []
    assert less_coverage_problems(result(run(0, 1), run(3, 4)), after, "coverage")


@pytest.mark.parametrize("operation", [SweepIndex, probe_starts])
def test_work_limit_propagates_from_index_or_probe_creation(operation):
    with pytest.raises(WorkLimit):
        operation(result(run(0, 1)), meter=CountingMeter(limit=0))


@pytest.mark.parametrize("comparison", [more_error_problems, less_coverage_problems])
def test_work_limit_propagates_from_comparison(comparison):
    answer = result(run(0, 1, "pass"))
    with pytest.raises(WorkLimit):
        comparison(answer, answer, "change", meter=CountingMeter(limit=1))


def test_probe_midpoint_does_not_overflow_finite_endpoints():
    assert probe_starts(result(run(1e308, 1e308))) == [("w1", 1e308)]
