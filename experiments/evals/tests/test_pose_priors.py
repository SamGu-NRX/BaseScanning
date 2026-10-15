"""Hand-computed cases for the pose-prior uncertainty: pooled percentiles, the group and noise-draw
bootstrap, pooling over draws, the ADVIO scales, and the fit cache."""

import json
from pathlib import Path

import numpy as np
import pytest

from evals.ar_poses import ADVIO_ARKIT_SCALES, SETTINGS, group_poses, noise_draws
from evals.pairs import INCH
from evals.pose_priors import (
    SENTINEL,
    FitCache,
    bootstrap_interval,
    pooled_percentiles,
    summarize_groups,
)

RESULTS = Path(__file__).resolve().parents[1] / "results"


def test_pooled_percentiles_repeat_each_value_by_its_weight():
    # Weights 2, 0, 1, 3 on 1, 2, 3, 4 expand to [1, 1, 3, 4, 4, 4]:
    # median at position 2.5 -> (3 + 4) / 2; p90 at 4.5 -> 4; p0 -> 1.
    v = np.array([1.0, 2.0, 3.0, 4.0])
    out = pooled_percentiles(v, np.arange(4), np.array([2, 0, 1, 3]), (50, 90, 0))
    np.testing.assert_allclose(out, [3.5, 4.0, 1.0])


def test_pooled_percentile_touching_a_failure_is_a_failure():
    # [1, failed]: the median interpolates halfway into the failure; p0 is the finite value.
    out = pooled_percentiles(np.array([1.0, SENTINEL]), np.arange(2), np.array([1, 1]), (50, 0))
    assert np.isinf(out[0]) and out[1] == 1.0


def test_one_group_one_draw_has_no_spread():
    # Every replicate is [1, 2, 3]: median 2, p90 at position 1.8 -> 2.8.
    (m, p) = bootstrap_interval([[np.array([1.0, 2.0, 3.0])]], reps=50)
    assert m == (2.0, 2.0) and p == (2.8, 2.8)


def test_groups_are_resampled():
    # Replicates: AA -> 1 (p = 1/4), BB -> 3 (1/4), AB -> [1, 1, 3, 3]: median 2, p90 3 (1/2).
    # With 25% of the mass at each end, the 2.5% and 97.5% order statistics are 1 and 3.
    (m, p) = bootstrap_interval([[np.array([1.0, 1.0])], [np.array([3.0, 3.0])]])
    assert m == (1.0, 3.0) and p == (1.0, 3.0)


def test_noise_draws_are_resampled_within_a_group():
    # One group, two draws: the same three outcomes as two groups with one draw each.
    (m, _) = bootstrap_interval([[np.array([1.0, 1.0]), np.array([3.0, 3.0])]])
    assert m == (1.0, 3.0)
    # Identical draws carry no noise-draw spread.
    (m, _) = bootstrap_interval([[np.array([1.0, 3.0]), np.array([1.0, 3.0])]], reps=50)
    assert m == (2.0, 2.0)


def test_failures_make_the_interval_end_infinite():
    # Group B fails every pair: BB and AB replicates have an infinite p90.
    (_, p) = bootstrap_interval([[np.array([1.0, 1.0])], [np.array([np.inf, np.inf])]])
    assert p[0] == 1.0 and np.isinf(p[1])


def _raw(errors_in):
    e = np.asarray(errors_in, float) * INCH
    ratios = 1 + e / 2.0  # every pair 2 m long
    return {
        "none": {"1-3m": (e, ratios)},
        "one_known_distance": {"1-3m": (e, ratios)},
        "refs": 0,
        "ref_failures": 0,
    }


def test_summary_pools_every_draw_of_every_group():
    # One group, two noise draws of errors 1 and 3 in: pooled median 2 in, not a mean of medians
    # computed some other way; one group -> no spread from groups, only from draws.
    s = summarize_groups([[_raw([1.0, 1.0]), _raw([3.0, 3.0])]])
    assert s["groups"] == 1 and s["noise_draws"] == 2
    assert s["none"]["1-3m"]["median_in"] == 2.0
    assert s["interval_95"]["1-3m"]["median_in"] == [1.0, 3.0]
    # Scale error: ratios 1 + 0.0254/2 and 1 + 0.0762/2, median 1 + 0.0254 -> +2.54%.
    assert s["none"]["1-3m"]["scale_error_pct"] == pytest.approx(2.54)


def test_advio_scales_are_the_drift_results():
    drift = json.loads((RESULTS / "advio_drift.json").read_text())
    measured = {r["sequence"]: r["arkit_scale_vs_truth_gps"] for r in drift["sequences"][:3]}
    assert list(measured) == [20, 21, 22]
    np.testing.assert_allclose(ADVIO_ARKIT_SCALES, list(measured.values()), atol=5e-5)
    assert SETTINGS["advio_2018"].scales == ADVIO_ARKIT_SCALES


def test_noise_free_settings_get_one_draw():
    assert len(noise_draws("exact")) == 1
    assert len(noise_draws("advio_2018")) == len(noise_draws("modern_assumed")) == 5


class _V:
    def __init__(self, x):
        self.cam_to_world = np.eye(4)
        self.cam_to_world[0, 3] = x


def test_pose_draws_are_repeatable_and_differ_between_draws():
    views = {"a": _V(0.0), "b": _V(2.0)}
    groups = {"2": [["a", "b"]]}
    d0 = group_poses(views, groups, "modern_assumed", 0)["n2-a"]
    assert d0 == group_poses(views, groups, "modern_assumed", 0)["n2-a"]
    d1 = group_poses(views, groups, "modern_assumed", 1)["n2-a"]
    assert d0["poses"] != d1["poses"]
    assert d0["scale"] == d1["scale"]


def test_fit_cache_refits_only_when_inputs_change(tmp_path):
    calls = []

    def fit():
        calls.append(1)
        return {"a": 0.9}

    cache = FitCache(tmp_path / "fits.json")
    assert cache.get("k", "h1", fit) == {"a": 0.9}
    cache.save()
    again = FitCache(tmp_path / "fits.json")
    assert again.get("k", "h1", fit) == {"a": 0.9} and len(calls) == 1
    again.get("k", "h2", fit)
    assert len(calls) == 2
