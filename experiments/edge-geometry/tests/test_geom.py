import numpy as np
import pytest

from geom import (
    angle_bin,
    decompose,
    fit_plane,
    midpoint_triangulate,
    ray_plane,
    signed_yaw,
    view_angle_deg,
    wall_frame,
    yaw_shift,
)

UP = np.array([0.0, 0.0, 1.0])


def test_ray_plane_hits_known_point():
    n, d = np.array([0.0, 1.0, 0.0]), 3.0  # the plane y = 3
    X = ray_plane(np.array([1.0, 0.0, 2.0]), np.array([[1.0, 1.0, 0.0], [0.0, 2.0, -1.0]]), n, d)
    np.testing.assert_allclose(X, [[4.0, 3.0, 2.0], [1.0, 3.0, 0.5]], atol=1e-12)


def test_ray_plane_rejects_parallel_and_behind():
    n, d = np.array([0.0, 1.0, 0.0]), 3.0
    X = ray_plane(np.zeros(3), np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0]]), n, d)
    assert np.isnan(X).all()


def test_decompose_splits_error_along_and_across_the_wall():
    n = np.array([0.0, 1.0, 0.0])
    # h = up x n = (0,0,1) x (0,1,0) = (-1, 0, 0)
    np.testing.assert_allclose(wall_frame(n, UP)[0], [-1.0, 0.0, 0.0])
    parts = decompose(np.array([[0.02, -0.03, 0.5]]), n, UP)
    assert parts["along"][0] == pytest.approx(-0.02)
    assert parts["out"][0] == pytest.approx(-0.03)
    assert parts["3d"][0] == pytest.approx(np.sqrt(0.02**2 + 0.03**2 + 0.5**2))


def test_decompose_on_a_tilted_wall_keeps_along_horizontal():
    tilt = np.radians(10)
    n = np.array([0.0, np.cos(tilt), np.sin(tilt)])
    frame = wall_frame(n, UP)
    assert frame[0] @ UP == pytest.approx(0.0)
    assert frame[0] @ n == pytest.approx(0.0)
    np.testing.assert_allclose(frame @ frame.T, np.eye(3), atol=1e-12)
    # A vertical error has no along-wall part.
    assert decompose(np.array([[0.0, 0.0, 0.1]]), n, UP)["along"][0] == pytest.approx(0.0)


def test_view_angle_and_bins():
    n = np.array([0.0, 1.0, 0.0])
    rays = np.array([[0.0, -1.0, 0.0], [np.sin(np.radians(20)), np.cos(np.radians(20)), 0.0]])
    np.testing.assert_allclose(view_angle_deg(rays, n), [0.0, 20.0], atol=1e-9)
    assert list(angle_bin([0.0, 14.999, 15.0, 29.9, 30.0, 45.0, 89.0, 90.0])) == [
        0,
        0,
        1,
        1,
        2,
        3,
        3,
        3,
    ]
    with pytest.raises(ValueError):
        angle_bin([91.0])


def test_fit_plane_ignores_outliers():
    rng = np.random.default_rng(0)
    xy = rng.uniform(-1, 1, size=(500, 2))
    pts = np.c_[xy[:, 0], np.full(500, 2.0) + rng.normal(0, 0.002, 500), xy[:, 1]]
    pts[:40, 1] += 0.5  # a box in front of the wall
    n, d = fit_plane(pts)
    assert abs(n[1]) == pytest.approx(1.0, abs=1e-4)
    assert d * np.sign(n[1]) == pytest.approx(2.0, abs=1e-3)


def test_signed_yaw_and_yaw_shift_match_a_rotated_plane():
    n = np.array([0.0, 1.0, 0.0])
    psi = np.radians(2.0)
    c, s = np.cos(psi), np.sin(psi)
    Rz = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    n_rot = Rz @ n
    assert signed_yaw(n_rot, n, UP) == pytest.approx(psi)
    assert signed_yaw(-n_rot, n, UP) == pytest.approx(psi)  # sign of the normal does not matter

    # Wall y = 3; anchor at x = 0; edge 2 m along the wall; camera looking obliquely at the edge.
    anchor = np.array([0.0, 3.0, 1.0])
    edge = np.array([2.0, 3.0, 1.0])
    cam = np.array([-1.0, 0.0, 1.0])
    ray = (edge - cam)[None]
    hit = ray_plane(cam, ray, n_rot, float(n_rot @ anchor))[0]
    along_err = decompose((hit - edge)[None], n, UP)["along"][0]
    h = wall_frame(n, UP)[0]
    predicted = yaw_shift(h @ (edge - anchor), psi, ray, n, UP)[0]
    assert predicted == pytest.approx(along_err, rel=0.05)


def test_midpoint_triangulation():
    X = np.array([0.3, -0.2, 4.0])
    c1, c2 = np.zeros(3), np.array([0.5, 0.0, 0.0])
    got, angle = midpoint_triangulate(c1, X - c1, c2, X - c2)
    np.testing.assert_allclose(got, X, atol=1e-12)
    r1, r2 = X / np.linalg.norm(X), (X - c2) / np.linalg.norm(X - c2)
    assert angle == pytest.approx(np.degrees(np.arccos(r1 @ r2)))


def test_midpoint_of_skew_rays_is_the_middle_of_their_common_perpendicular():
    # x-axis ray from (-1, 0, 0) and a ray along -y from (0, 1, 1): closest at (0,0,0) and (0,0,1).
    got, angle = midpoint_triangulate(
        np.array([-1.0, 0, 0]),
        np.array([1.0, 0, 0]),
        np.array([0, 1.0, 1.0]),
        np.array([0, -1.0, 0]),
    )
    np.testing.assert_allclose(got, [0.0, 0.0, 0.5], atol=1e-12)
    assert angle == pytest.approx(90.0)


def test_midpoint_triangulation_rejects_parallel_and_behind():
    got, _ = midpoint_triangulate(
        np.zeros(3), np.array([0, 0, 1.0]), np.ones(3), np.array([0, 0, 1.0])
    )
    assert np.isnan(got).all()
    got, _ = midpoint_triangulate(
        np.zeros(3), np.array([0, 0, -1.0]), np.array([1.0, 0, 0]), np.array([-1.0, 0, 1.0])
    )
    assert np.isnan(got).all()
