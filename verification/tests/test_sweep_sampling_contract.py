import copy
from dataclasses import replace

import pytest
from test_resultcheck import RULES, SCENE, pass_run, result

from hsverify import resultcheck as checks


@pytest.mark.parametrize(
    "span,expected", [([0, 2.4], [0, 1, 2, 2.4]), ([0, 0.4], [0, 0.4]), ([2, 2], [2])]
)
def test_pre_end_count_plus_endpoint_matches_every_yield(span, expected):
    samples = list(checks.run_starts(span, 1))
    assert samples == expected
    assert len(samples) == checks.run_start_count(span, 1) + 1


@pytest.mark.parametrize("span,failing_start", [([0, 2.4], 2), ([0, 0.4], 0)])
def test_coverage_rejects_a_failure_at_the_required_pre_end_start(monkeypatch, span, failing_start):
    rules = replace(RULES, needs={"ground_surface": ()}, step_ft=1)
    scene = copy.deepcopy(SCENE)
    seen = []

    def gap(scene, rules, check, wall_id, lo, hi, meter=None):
        seen.append(lo)
        return [(None, "unobserved at required pre-end start")] if lo == failing_start else []

    monkeypatch.setattr(checks, "reach_gaps", gap)
    answer = result(spot=False, sweep=[pass_run(*span)])
    problems = checks.coverage_problems(scene, answer, rules)
    assert seen == list(checks.run_starts(span, 1))
    assert any("unobserved at required pre-end start" in problem for problem in problems)


def test_coverage_charges_once_for_each_yielded_start(monkeypatch):
    rules = replace(RULES, needs={"ground_surface": ()}, step_ft=1)
    sampled = []

    def gap(scene, rules, check, wall_id, lo, hi, meter=None):
        sampled.append((lo, meter.samples))
        return []

    monkeypatch.setattr(checks, "reach_gaps", gap)
    checks.coverage_problems(SCENE, result(spot=False, sweep=[pass_run(0, 2.4)]), rules)
    assert [start for start, _ in sampled] == [0, 1, 2, 2.4]
    assert [count - sampled[0][1] for _, count in sampled] == [0, 1, 2, 3]
