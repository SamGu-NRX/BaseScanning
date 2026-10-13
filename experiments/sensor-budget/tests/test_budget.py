"""Strict checks of the closed-form models: each has one correct answer."""

from statistics import NormalDist

import numpy as np
import pytest

import budget as b


def fixed(values):
    return lambda name: np.full(b.N, values[name])


class ZeroRng:
    """A generator whose normals are all 1, so a model returns its 1-sigma value."""

    def standard_normal(self, n):
        return np.ones(n)


def test_dual_camera_depth_noise_formula():
    v = fixed(
        {"standoff_m": 2.0, "dual_disp_px": 0.25, "dual_f_px": 500.0, "dual_baseline_m": 0.02}
    )
    got = b._dual(v, ZeroRng())[0]
    assert got == pytest.approx(4.0 * 0.25 / (500 * 0.02) * b.IN_PER_M)


def test_focus_depth_noise_formula():
    v = fixed({"standoff_m": 2.0, "focal_mm": 6.0, "lens_pos_um": 1.0})
    got = b._focus(v, ZeroRng())[0]
    assert got == pytest.approx(4.0 * 1e-6 / 0.006**2 * b.IN_PER_M)


def test_uwb_error_grows_as_the_span_shrinks():
    v = fixed({"uwb_span_ft": 10.0, "standoff_m": 3.048, "uwb_sigma_m": 0.1})
    got = b._uwb(v, ZeroRng())[0]
    assert got == pytest.approx(0.1 * np.sqrt(2) * b.IN_PER_M)


def test_anchor_plane_error_is_s_tan_psi_tan_alpha():
    v = fixed({"tap_span_ft": 6.0, "anchor_yaw_deg": 5.0, "tap_view_deg": 45.0})
    got = b._anchor_plane(v, ZeroRng())[0]
    assert got == pytest.approx(6 * b.FT * np.tan(np.deg2rad(5.0)) * b.IN_PER_M)


def test_one_photo_is_exact_without_error():
    v = fixed({"span_ft": 6.0, "one_photo_standoff_m": 3.0, "s": 0.0, "y": 0.0})

    class Zero:
        def standard_normal(self, n):
            return np.zeros(n)

    assert np.all(np.abs(b._one_photo("s", "y")(v, Zero())) < 1e-9)


def test_one_photo_yaw_term_matches_first_order():
    s, d, psi_deg = 6 * b.FT, 3.0, 0.5
    v = fixed({"span_ft": 6.0, "one_photo_standoff_m": d, "s": 0.0, "y": psi_deg})
    got = b._one_photo("s", "y")(v, ZeroRng())[0]
    first_order = np.deg2rad(psi_deg) * s**2 / d * b.IN_PER_M
    assert got == pytest.approx(first_order, rel=0.05)


@pytest.mark.parametrize(
    ("kind", "opt", "cons", "want"),
    [
        ("length", 1.0, 2.0, "worth a device test"),
        ("length", 1.0, 2.01, "in between"),
        ("length", 4.01, 9.0, "drop"),
        ("length", 4.0, 9.0, "in between"),
        ("scale", 0.5, 1.0, "worth a device test"),
        ("scale", 3.01, 5.0, "drop"),
    ],
)
def test_verdict_boundaries(kind, opt, cons, want):
    assert b.verdict(kind, opt, cons) == want


def test_empirical_yaw_endpoints_are_converted_to_gaussian_sigmas():
    parameter = b.PARAMS["anchor_yaw_deg"]
    assert parameter.opt * NormalDist().inv_cdf(.75) == pytest.approx(2.15)
    assert parameter.cons * NormalDist().inv_cdf(.95) == pytest.approx(6.72)
