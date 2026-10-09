"""Hardening cases for evals.triangulate and evals.ar_poses.

Beyond the happy paths in test_triangulate.py, test_fixed_sets.py and test_pose_priors.py: error
paths (bad shapes, improper rotations, unknown settings), degenerate geometry (points behind the
cameras, parallel rays, blank photos, empty inputs), and hand-computed matrices.
"""

import json
import warnings

import cv2
import numpy as np
import pytest

from evals.ar_poses import (
    ADVIO_ARKIT_SCALES,
    SETTINGS,
    PoseError,
    degrade,
    group_poses,
    noise_draws,
    small_rotation,
)
from evals.triangulate import (
    ScaleFit,
    match,
    projection,
    sample_depth,
    triangulate,
    triangulate_pair,
    view_scales,
)

F, W, H = 500.0, 640, 480
K0 = np.array([[F, 0, (W - 1) / 2], [0, F, (H - 1) / 2], [0, 0, 1.0]])


def _pose(center, R=None) -> np.ndarray:
    T = np.eye(4)
    if R is not None:
        T[:3, :3] = R
    T[:3, 3] = center
    return T


def _project(K, T, X):
    p = projection(K, T) @ np.r_[X, 1.0]
    return p[:2] / p[2]


def _plane_scene():
    """Three photos of a textured plane 4 m ahead (the construction from test_triangulate.py):
    cameras 0.4 m apart in x and y, so each photo is an integer 50 px crop of one texture and the
    true depth is 4 m at every pixel."""
    noise = np.random.default_rng(0).uniform(0, 255, (H + 100, W + 100)).astype(np.float32)
    tex = cv2.normalize(cv2.GaussianBlur(noise, (0, 0), 2.0), None, 0, 255, cv2.NORM_MINMAX)
    tex = tex.astype(np.uint8)
    images = {
        "a": tex[0:H, 0:W].copy(),
        "b": tex[0:H, 50 : 50 + W].copy(),
        "c": tex[50 : 50 + H, 0:W].copy(),
    }
    poses = {
        "a": _pose([0.0, 0.0, 0.0]),
        "b": _pose([0.4, 0.0, 0.0]),
        "c": _pose([0.0, 0.4, 0.0]),
    }
    return images, {n: K0 for n in images}, poses


# --- evals.triangulate.projection --------------------------------------------------------------


def test_projection_translation_only_hand_computed():
    T = _pose([1.0, 2.0, 3.0])
    # R = I, so t = -C = (-1, -2, -3): the last column is K @ t = (-500 - 3 * 319.5, -1000 - 3 *
    # 239.5, -3).
    P = projection(K0, T)
    np.testing.assert_allclose(
        P, [[500, 0, 319.5, -1458.5], [0, 500, 239.5, -1718.5], [0, 0, 1, -3]]
    )


def test_projection_rotation_block_is_world_to_camera():
    # 90 degrees about z: the camera's x axis looks along world +y.
    c, s = np.cos(np.pi / 2), np.sin(np.pi / 2)
    T = _pose([1.0, 0.0, 0.0], np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]))
    P = projection(K0, T)
    # World-to-camera rotation [[0,1,0],[-1,0,0],[0,0,1]], t = -R_wc @ C = (0, 1, 0), so the last
    # column is K @ (0, 1, 0) = (0, 500, 0).
    np.testing.assert_allclose(
        P[:3, :3], K0 @ np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]]), atol=1e-9
    )
    np.testing.assert_allclose(P[:, 3], [0, 500, 0], atol=1e-9)


# --- evals.triangulate.sample_depth ------------------------------------------------------------


def test_sample_depth_of_an_integer_depth_map():
    d = np.array([[10, 20], [30, 40]], np.uint16)
    # Bilinear at the centre of the 2x2 block is the mean of the four, as floats.
    np.testing.assert_allclose(sample_depth(d, np.array([[0.5, 0.5]])), [25.0])


def test_sample_depth_zero_and_negative_depth_are_holes():
    # A zero inside the stencil poisons it: blending across it would invent a value.
    zero = np.array([[1.0, 0.0], [3.0, 4.0]])
    assert np.isnan(sample_depth(zero, np.array([[0.5, 0.5]]))).all()
    negative = np.array([[-1.0, 2.0], [3.0, 4.0]])
    assert np.isnan(sample_depth(negative, np.array([[0.0, 0.0]]))).all()


def test_sample_depth_nonfinite_or_huge_uv_is_nan_without_warning():
    # NaN, infinity and beyond-2^53 positions have no valid integer pixel index: they must come
    # back as NaN without casting them to int anyway (an undefined conversion, and it warns).
    d = np.array([[1.0, 2.0], [3.0, 4.0]])
    uv = np.array([[np.nan, 0.0], [np.inf, 1.0], [1.0, -np.inf], [1e300, 0.0]])
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        got = sample_depth(d, uv)
    assert np.isnan(got).all()


def test_sample_depth_empty_uv():
    got = sample_depth(np.ones((4, 4)), np.zeros((0, 2)))
    assert got.shape == (0,)


# --- evals.triangulate.triangulate -------------------------------------------------------------


def test_triangulate_recovers_a_batch_of_points():
    c, s = np.cos(np.radians(10)), np.sin(np.radians(10))
    T1 = _pose([0.0, 0.0, 0.0])
    T2 = _pose([1.0, 0.1, 0.2], np.array([[c, 0, -s], [0, 1, 0], [s, 0, c]]))
    X = np.array([[0.3, -0.2, 5.0], [-0.4, 0.1, 4.0], [0.0, 0.0, 3.0]])
    x1 = np.array([_project(K0, T1, p) for p in X])
    x2 = np.array([_project(K0, T2, p) for p in X])
    got = triangulate(projection(K0, T1), projection(K0, T2), x1, x2)
    np.testing.assert_allclose(got, X, atol=1e-9)


def test_triangulate_no_correspondences():
    P = projection(K0, np.eye(4))
    got = triangulate(P, P, np.zeros((0, 2)), np.zeros((0, 2)))
    assert got.shape == (0, 3)


# --- evals.triangulate.triangulate_pair --------------------------------------------------------


def test_triangulate_pair_rejects_a_point_behind_the_cameras():
    # The projections of (0, 0, -5) are well-formed pixels (the perspective division flips the
    # sign), and the DLT still intersects the rays - behind the cameras. Only the in-front check
    # can reject this one.
    X = np.array([0.0, 0.0, -5.0])
    T1, T2 = np.eye(4), _pose([1.0, 0.0, 0.0])
    x1, x2 = _project(K0, T1, X)[None], _project(K0, T2, X)[None]
    pts = triangulate_pair(K0, T1, K0, T2, x1, x2, max_reproj_px=2.0, min_angle_deg=2.0)
    np.testing.assert_allclose(pts.X[0], X, atol=1e-9)
    assert not pts.keep[0]


def test_triangulate_pair_rejects_parallel_rays():
    # Both cameras see the principal point: the rays are parallel, so the point is at infinity and
    # its reprojection is perfect. Only the angle test can reject it.
    T1, T2 = np.eye(4), _pose([1.0, 0.0, 0.0])
    x = np.array([[(W - 1) / 2, (H - 1) / 2]])
    pts = triangulate_pair(K0, T1, K0, T2, x, x, max_reproj_px=2.0, min_angle_deg=2.0)
    assert not np.isfinite(pts.X).all()
    assert not pts.keep[0]


def test_triangulate_pair_no_correspondences():
    pts = triangulate_pair(
        K0,
        np.eye(4),
        K0,
        _pose([1.0, 0.0, 0.0]),
        np.zeros((0, 2)),
        np.zeros((0, 2)),
        max_reproj_px=2.0,
        min_angle_deg=2.0,
    )
    assert pts.X.shape == (0, 3)
    assert pts.keep.shape == (0,)


# --- evals.triangulate.match -------------------------------------------------------------------


def test_match_requires_mutuality():
    # p, q and r all pass the ratio test against s (their second best, t, is far away), but s's
    # best match back into the left set is p: q and r are dropped, p is kept.
    d1 = np.array([[0, 10], [0, 11], [10, 0]], np.float32)
    d2 = np.array([[0, 10.4], [100, 100]], np.float32)
    np.testing.assert_array_equal(match(d1, d2), [[0, 0]])


def test_match_ratio_test_drops_ambiguous_descriptors():
    # Two identical right-hand descriptors: both are equally good (distance 0), and 0 < 0.8 * 0 is
    # false, so the ambiguous descriptor is dropped.
    d1 = np.array([[0, 10], [5, 5]], np.float32)
    d2 = np.array([[0, 10], [0, 10]], np.float32)
    assert len(match(d1, d2)) == 0


def test_match_ratio_parameter():
    # Best distance 1, second best 1.15: dropped at the default ratio, kept at 0.9.
    d1 = np.array([[0, 3], [9, 9]], np.float32)
    d2 = np.array([[0, 2], [0, 4.15]], np.float32)
    assert len(match(d1, d2)) == 0
    np.testing.assert_array_equal(match(d1, d2, ratio=0.9), [[0, 0]])


@pytest.mark.parametrize("n1, n2", [(0, 2), (2, 0), (1, 2), (2, 1), (1, 1), (0, 0)])
def test_match_too_few_descriptors_on_either_side(n1, n2):
    rng = np.random.default_rng(0)
    d1 = rng.uniform(0, 1, (n1, 8)).astype(np.float32)
    d2 = rng.uniform(0, 1, (n2, 8)).astype(np.float32)
    np.testing.assert_array_equal(match(d1, d2), np.empty((0, 2), np.int64))


# --- evals.triangulate.view_scales -------------------------------------------------------------


def test_view_scales_fit_each_photo_separately():
    """Planted depth scales differ per photo (1.1, 0.8, 1.0 on the true 4 m): each fit must recover
    the reciprocal of its own photo's factor, not blend ratios across photos."""
    images, K, poses = _plane_scene()
    depth = {"a": np.full((H, W), 4.4), "b": np.full((H, W), 3.2), "c": np.full((H, W), 4.0)}
    fits = view_scales(images, K, poses, depth)
    assert fits["a"].scale == pytest.approx(1 / 1.1, rel=1e-4)
    assert fits["b"].scale == pytest.approx(1 / 0.8, rel=1e-4)
    assert fits["c"].scale == pytest.approx(1.0, rel=1e-4)
    for fit in fits.values():
        assert fit.spread < 0.01


def test_view_scales_blank_photo_gets_no_fit_and_does_not_disturb_the_others():
    images, K, poses = _plane_scene()
    images["d"] = np.full((H, W), 128, np.uint8)  # no texture, no keypoints, desc None
    poses["d"] = _pose([0.0, 0.8, 0.0])
    K["d"] = K0
    depth = {n: np.full((H, W), 4.0) for n in images}
    fits = view_scales(images, K, poses, depth)
    assert fits["d"] == ScaleFit(scale=None, points=0, spread=None)
    for n in "abc":
        assert fits[n].scale == pytest.approx(1.0, rel=1e-4)
    # Even with no minimum, a photo with no usable ratios gets no scale.
    assert view_scales(images, K, poses, depth, min_points=1)["d"].scale is None


def test_view_scales_rejects_a_single_photo():
    images, K, poses = _plane_scene()
    with pytest.raises(ValueError, match="at least 2"):
        view_scales(
            {"a": images["a"]}, {"a": K["a"]}, {"a": poses["a"]}, {"a": np.full((H, W), 4.0)}
        )


def test_view_scales_rejects_improper_rotations():
    images, K, poses = _plane_scene()
    depth = {n: np.full((H, W), 4.0) for n in images}
    # A reflection is orthogonal but not a rotation (det = -1).
    poses["b"] = _pose([0.4, 0.0, 0.0], np.diag([1.0, 1.0, -1.0]))
    with pytest.raises(ValueError, match="proper rotation"):
        view_scales(images, K, poses, depth)
    # A stretched frame is not even orthogonal.
    poses["b"] = _pose([0.4, 0.0, 0.0], 2.0 * np.eye(3))
    with pytest.raises(ValueError, match="proper rotation"):
        view_scales(images, K, poses, depth)


def test_view_scales_rejects_bad_shapes_and_dtypes():
    images, K, poses = _plane_scene()
    depth = {n: np.full((H, W), 4.0) for n in images}
    K["b"] = np.eye(2)
    with pytest.raises(ValueError, match="K must be 3x3"):
        view_scales(images, K, poses, depth)
    K["b"] = K0
    poses["b"] = np.eye(3)
    with pytest.raises(ValueError, match="cam_to_world must be 4x4"):
        view_scales(images, K, poses, depth)
    poses["b"] = _pose([0.4, 0.0, 0.0])
    float_image = dict(images)
    float_image["b"] = images["b"].astype(np.float32)
    with pytest.raises(ValueError, match="grayscale uint8"):
        view_scales(float_image, K, poses, depth)
    colour_image = dict(images)
    colour_image["b"] = np.stack([images["b"]] * 3, axis=-1)
    with pytest.raises(ValueError, match="grayscale uint8"):
        view_scales(colour_image, K, poses, depth)


# --- evals.ar_poses.small_rotation -------------------------------------------------------------


def test_small_rotation_zero_vector_is_identity():
    np.testing.assert_array_equal(small_rotation(np.zeros(3)), np.eye(3))


def test_small_rotation_ninety_degrees_about_x_and_y():
    np.testing.assert_allclose(
        small_rotation(np.radians([90.0, 0.0, 0.0])),
        [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
        atol=1e-12,
    )
    np.testing.assert_allclose(
        small_rotation(np.radians([0.0, 90.0, 0.0])),
        [[0, 0, 1], [0, 1, 0], [-1, 0, 0]],
        atol=1e-12,
    )


def test_small_rotation_is_a_rotation_about_the_given_axis():
    v = np.array([0.2, -0.5, 1.0])
    theta = np.linalg.norm(v)
    k = v / theta
    R = small_rotation(v)
    np.testing.assert_allclose(R @ k, k, atol=1e-12)  # the axis does not move
    np.testing.assert_allclose(R.T @ R, np.eye(3), atol=1e-12)
    assert np.linalg.det(R) == pytest.approx(1.0)
    # The rotation angle is the vector's length, for any angle, not only small ones.
    assert np.trace(R) == pytest.approx(1 + 2 * np.cos(theta))


# --- evals.ar_poses.degrade --------------------------------------------------------------------


def test_degrade_scales_offsets_about_the_first_camera_wherever_it_is():
    # The first camera is not at the origin: it stays put (the noise is zero here), and the second
    # camera's offset from it halves.
    T0, T1 = np.eye(4), np.eye(4)
    T0[:3, 3] = [10.0, 20.0, 30.0]
    T1[:3, 3] = [12.0, 20.0, 34.0]
    out = degrade([T0, T1], 0.5, SETTINGS["exact"], np.random.default_rng(0))
    np.testing.assert_allclose(out[0][:3, 3], [10.0, 20.0, 30.0])
    np.testing.assert_allclose(out[1][:3, 3], [11.0, 20.0, 32.0])
    np.testing.assert_allclose(out[1][:3, :3], np.eye(3))
    # Pure function: the inputs are untouched and the outputs are new arrays.
    np.testing.assert_allclose(T1[:3, 3], [12.0, 20.0, 34.0])
    assert out[0] is not T0
    # Scale 1 with no noise returns the poses unchanged.
    same = degrade([T0, T1], 1.0, SETTINGS["exact"], np.random.default_rng(0))
    np.testing.assert_allclose(same[1], T1)


def test_degrade_position_noise_moves_positions_but_not_rotations():
    error = PoseError(
        scales=(1.0,), position_sigma_m=0.05, rotation_sigma_deg=0.0, reprojection_px=8.0
    )
    T1 = np.eye(4)
    T1[:3, 3] = [3.0, 0.0, 0.0]
    out = degrade([np.eye(4), T1], 1.0, error, np.random.default_rng(1))
    for got, ref in zip(out, [np.eye(4), T1], strict=True):
        np.testing.assert_array_equal(got[:3, :3], ref[:3, :3])  # exactly
        assert not np.array_equal(got[:3, 3], ref[:3, 3])  # moved
        # 3 axis draws of sigma 0.05: a norm over 0.5 would be ten sigmas on every axis.
        assert np.linalg.norm(got[:3, 3] - ref[:3, 3]) < 0.5
    # The first camera is not pinned: it takes its own noise draw.
    assert not np.array_equal(out[0][:3, 3], [0.0, 0.0, 0.0])


def test_degrade_rotation_noise_turns_cameras_about_their_own_centres():
    error = PoseError(
        scales=(1.0,), position_sigma_m=0.0, rotation_sigma_deg=5.0, reprojection_px=8.0
    )
    T0, T1 = np.eye(4), np.eye(4)
    T1[:3, 3] = [2.0, 0.0, 0.0]
    out = degrade([T0, T1], 1.0, error, np.random.default_rng(2))
    for got, ref in zip(out, [T0, T1], strict=True):
        np.testing.assert_array_equal(got[:3, 3], ref[:3, 3])  # exactly the scaled positions
        assert not np.array_equal(got[:3, :3], ref[:3, :3])
        np.testing.assert_allclose(got[:3, :3].T @ got[:3, :3], np.eye(3), atol=1e-12)


# --- evals.ar_poses.group_poses and the constants ----------------------------------------------


class _V:
    """A minimal view: group_poses only reads cam_to_world."""

    def __init__(self, x):
        self.cam_to_world = np.eye(4)
        self.cam_to_world[0, 3] = x


_VIEWS = {"a": _V(0.0), "b": _V(2.0), "c": _V(4.0), "d": _V(6.0), "e": _V(8.0)}
_GROUPS = {
    "2": [["a", "b"]],
    "4": [["a", "c", "d"]],
    "8": [["b", "c", "d", "e"]],
    "9": [["a", "e"]],
}


def test_group_poses_exact_setting_returns_the_true_poses_for_any_draw():
    out0 = group_poses(_VIEWS, _GROUPS, "exact", 0)
    out3 = group_poses(_VIEWS, _GROUPS, "exact", 3)
    assert out0 == out3  # a noise-free setting has nothing to redraw
    assert out0["n2-a"]["scale"] == 1.0
    np.testing.assert_array_equal(np.array(out0["n4-a"]["poses"]["d"]), _VIEWS["d"].cam_to_world)


def test_group_poses_hands_the_advio_scales_out_in_group_order():
    out = group_poses(_VIEWS, _GROUPS, "advio_2018", 0)
    assert list(out) == ["n2-a", "n4-a", "n8-b", "n9-a"]
    # One walk's scale per group, taken in turn across the whole call and wrapping around.
    expected = [ADVIO_ARKIT_SCALES[i % len(ADVIO_ARKIT_SCALES)] for i in range(len(_GROUPS))]
    assert [out[k]["scale"] for k in out] == expected


def test_group_poses_scale_is_stable_across_draws_but_poses_move():
    out0 = group_poses(_VIEWS, _GROUPS, "advio_2018", 0)
    out1 = group_poses(_VIEWS, _GROUPS, "advio_2018", 1)
    assert [out0[k]["scale"] for k in out0] == [out1[k]["scale"] for k in out1]
    assert not np.allclose(
        np.array(out0["n2-a"]["poses"]["b"]), np.array(out1["n2-a"]["poses"]["b"])
    )
    # Repeating a call reproduces the same poses bit for bit.
    assert group_poses(_VIEWS, _GROUPS, "advio_2018", 0) == out0


def test_group_poses_output_is_json_round_trippable():
    out = group_poses(_VIEWS, _GROUPS, "modern_assumed", 0)
    back = json.loads(json.dumps(out))
    assert back["n2-a"]["scale"] == out["n2-a"]["scale"]
    np.testing.assert_allclose(
        np.array(back["n2-a"]["poses"]["b"]), np.array(out["n2-a"]["poses"]["b"])
    )


def test_group_poses_and_noise_draws_reject_unknown_settings():
    with pytest.raises(KeyError):
        group_poses(_VIEWS, _GROUPS, "bogus")
    with pytest.raises(KeyError):
        noise_draws("bogus")


def test_advio_scales_match_the_documented_walks():
    # Module docstring: ARKit against the GPS-rescaled truth on walks 20 to 22, 0.838, 0.943 and
    # 0.951 to three decimals.
    np.testing.assert_allclose(ADVIO_ARKIT_SCALES, (0.838, 0.943, 0.951), atol=5e-4)


def test_settings_pin_the_documented_assumptions():
    assert SETTINGS["exact"] == PoseError((1.0,), 0.0, 0.0, 2.0)
    assert SETTINGS["modern_assumed"] == PoseError((0.98,), 0.01, 0.1, 3.0)
    advio = SETTINGS["advio_2018"]
    assert advio.scales == ADVIO_ARKIT_SCALES
    assert advio.position_sigma_m == 0.05
    assert advio.rotation_sigma_deg == 0.2
    # The more pose noise a setting carries, the looser the reprojection gate on matches made
    # with it.
    assert (
        SETTINGS["exact"].reprojection_px
        < SETTINGS["modern_assumed"].reprojection_px
        < advio.reprojection_px
    )
