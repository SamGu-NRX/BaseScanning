import math

import numpy as np
import pytest

import audit
from models import (
    DRIFT_PER_FT,
    LOCAL_P90_IN_AT_6FT,
    TAP_BASE_FT,
    Endpoint,
    at_least,
    common_mode_oracle_bar,
    current_bar,
    decide,
    local_clearance_bar,
    rate_on_gap_bar,
    rate_only_bar,
    scale_error_bar_factory,
)
from trajectories import MODERN, PHONE_2018, calibrated_k, implied_k, tracked_pair


def test_bars_at_worked_example():
    a, b = Endpoint(walked_ft=15), Endpoint(walked_ft=18)
    c = 3.0
    assert current_bar(a, b, c) == pytest.approx(5.88)
    assert current_bar(a, b, c) == pytest.approx(
        (TAP_BASE_FT + DRIFT_PER_FT * 15) + (TAP_BASE_FT + DRIFT_PER_FT * 18)
    )
    assert rate_only_bar(a, b, c) == pytest.approx(5.28)
    # The rate applied to the gap instead of the walks: bases plus 0.16 * 3.
    assert rate_on_gap_bar(a, b, c) == pytest.approx(2 * TAP_BASE_FT + DRIFT_PER_FT * 3)
    assert rate_on_gap_bar(a, b, c) == pytest.approx(1.08)
    # Measured-scale bars, per class, in feet.
    modern_bar = scale_error_bar_factory(MODERN.scale_sd)
    advio_bar = scale_error_bar_factory(PHONE_2018.scale_sd)
    assert modern_bar(a, b, c) == pytest.approx(2 * TAP_BASE_FT + MODERN.scale_sd * 3)
    assert advio_bar(a, b, c) == pytest.approx(2 * TAP_BASE_FT + PHONE_2018.scale_sd * 3)
    assert local_clearance_bar(a, b, c) == pytest.approx(LOCAL_P90_IN_AT_6FT["no_lidar"] / 24)


def test_at_least_mirrors_solver():
    # PASS when value - error > threshold; FAIL when value + error < threshold;
    # UNSURE in between or when the bar covers both lines.
    assert at_least(4.0, 0.5, 3.0) == "pass"
    assert at_least(2.0, 0.5, 3.0) == "fail"
    assert at_least(2.0, 2.0, 3.0) == "unsure"
    assert at_least(4.0, 2.0, 3.0) == "unsure"
    # On either line exactly (within EPS): UNSURE.
    assert at_least(3.5, 0.5, 3.0) == "unsure"


def test_decide_threshold_parameter():
    assert decide(10.0, 1.0, 8.0) == "pass"
    assert decide(6.0, 1.0, 8.0) == "fail"


def test_pure_scale_changes_clearance_exactly_by_scale_times_length():
    # With zero residual, the tracked gap differs from the true gap by exactly
    # the walk's scale error times the separation.
    rng = np.random.default_rng(1)
    s1, s2, k, n = 20.0, 23.0, 0.0, 1
    p1, p2, eps = tracked_pair(rng, MODERN, k, s1, s2, math.inf, n)
    d = float((p2 - p1)[0])
    assert d == pytest.approx((1 + float(eps[0])) * (s2 - s1))
    assert abs(d - (s2 - s1)) == pytest.approx(abs(float(eps[0])) * (s2 - s1))


def test_oracle_bar_is_scale_times_length():
    assert common_mode_oracle_bar(0.02, 3.0) == pytest.approx(0.06)
    assert common_mode_oracle_bar(-0.17, 3.0) == pytest.approx(0.51)


def test_cross_session_wrong_clear_with_measured_scales():
    # Two walks whose scales sit at opposite ends of the ADVIO class's
    # measured range, -17% and -5%. A violated 2.9 ft gap (features at 20 and
    # 22.9 ft) reads 2.26 ft longer than it is; the rate-on-gap bar (1.08 ft)
    # cannot cover it, and neither can the scale-spread bar.
    s1, s2 = 20.0, 22.9
    eps_a, eps_b = -0.17, -0.05
    true_gap = s2 - s1
    p1 = (1 + eps_a) * s1
    p2 = (1 + eps_b) * s2
    measured_gap = p2 - p1
    assert true_gap == pytest.approx(2.9)
    assert measured_gap - true_gap == pytest.approx(eps_b * s2 - eps_a * s1)
    bar = rate_on_gap_bar(Endpoint(walked_ft=s1), Endpoint(walked_ft=s2), true_gap)
    assert bar == pytest.approx(1.064)
    assert decide(measured_gap, bar) == "pass"  # wrongly clears a violated rule
    scale_bar = scale_error_bar_factory(PHONE_2018.scale_sd)(
        Endpoint(walked_ft=s1), Endpoint(walked_ft=s2), true_gap
    )
    assert scale_bar < measured_gap - true_gap
    assert decide(measured_gap, scale_bar) == "pass"


def test_calibration_is_conservative_at_fit_distances():
    rng = np.random.default_rng(7)
    for cls in (MODERN, PHONE_2018):
        k = calibrated_k(cls)
        assert k == pytest.approx(max(implied_k(cls, w) for w in cls.fit_distances_ft))
        for w in cls.fit_distances_ft:
            eps = rng.normal(cls.scale_mu, cls.scale_sd, size=200_000)
            g = rng.standard_normal(200_000)
            p90 = float(np.quantile(np.abs(w * (eps + k * g)), 0.9)) * 12
            # Conservative direction: the simulation may overstate the committed
            # p90 but must not understate it by more than rounding.
            assert p90 >= cls.pos_p90_in[w] * 0.98, (cls.name, w, p90)


def test_residual_correlation_bounds_the_difference():
    # For a fixed marginal spread, the difference error at correlation length
    # ell is monotone between the common (ell=inf) and independent extremes.
    rng = np.random.default_rng(3)
    k, s1, s2, n = 0.03, 20.0, 23.0, 200_000
    var_common = k**2 * (s2 - s1) ** 2
    var_indep = k**2 * (s1**2 + s2**2)
    for ell in (2.0, 5.0, 10.0, 20.0):
        p1, p2, _ = tracked_pair(rng, MODERN, k, s1, s2, ell, n)
        var_diff = float(np.var(p2 - p1))
        assert var_common <= var_diff * 1.001
        assert var_diff <= var_indep * 1.001


def test_audit_manifest_resolves():
    rows = audit.audit_rows()
    assert len(rows) >= 20
    assert rows[0]["quantity"] == "drift_per_ft rate"
    # The scale check recomputes the simulation's SD from the pinned arrays.
    sc = audit.scale_check()
    assert sc["n_walks"] == 25
    assert abs(sc["sd"] - 0.0147) < 0.002


def test_difference_scan_runs():
    scan = audit.scan_difference_evidence()
    assert scan["files_scanned"] >= 5
    assert isinstance(scan["matches"], list)
