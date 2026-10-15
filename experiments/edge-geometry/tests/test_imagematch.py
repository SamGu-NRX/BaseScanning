import cv2
import numpy as np
import pytest

from geom import midpoint_triangulate
from imagematch import epipolar_match, pixel_ray, project, snap, vertical_edge_strength

K = np.array([[560.0, 0, 320.0], [0, 560.0, 240.0], [0, 0, 1.0]])


def step_image(edge_after_col: int, width: int = 200, height: int = 120) -> np.ndarray:
    img = np.full((height, width), 0.2, np.float32)
    img[:, edge_after_col + 1 :] = 0.8
    return img


def test_snap_finds_a_vertical_step_to_sub_pixel():
    # Dark up to column 99, bright from 100: the edge sits at 99.5 in integer-centred pixels.
    strength = vertical_edge_strength(step_image(99))
    u, v = snap(strength, 112.4, 60.7, radius=25)
    assert u == pytest.approx(99.5, abs=0.05)
    assert v == 60.7


def test_snap_does_not_reach_past_its_radius():
    strength = vertical_edge_strength(step_image(99))
    assert snap(strength, 140.0, 60.0, radius=25) == (140.0, 60.0)


def test_snap_ignores_a_horizontal_edge():
    img = np.full((120, 200), 0.2, np.float32)
    img[60:, :] = 0.8
    assert snap(vertical_edge_strength(img), 100.0, 60.0) == (100.0, 60.0)


def look_at(center: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Camera-to-world pose at `center` looking at `target`, image y pointing down (world -y)."""
    z = target - center
    z /= np.linalg.norm(z)
    x = np.cross(np.array([0.0, -1.0, 0.0]), z)
    x /= np.linalg.norm(x)
    T = np.eye(4)
    T[:3, :3] = np.c_[x, np.cross(z, x), z]
    T[:3, 3] = center
    return T


def render_plane(T: np.ndarray, texture: np.ndarray, texel_m: float, z_plane: float) -> np.ndarray:
    """View of the textured plane z = z_plane (texture centred on the origin) from pose T."""
    h, w = 480, 640
    u, v = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
    d_cam = np.stack([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], np.ones_like(u)], -1)
    d = d_cam @ T[:3, :3].T
    t = (z_plane - T[2, 3]) / d[..., 2]
    X = T[:3, 3] + t[..., None] * d
    th, tw = texture.shape
    mx = (X[..., 0] / texel_m + tw / 2).astype(np.float32)
    my = (X[..., 1] / texel_m + th / 2).astype(np.float32)
    return cv2.remap(texture, mx, my, cv2.INTER_LINEAR)


def test_epipolar_search_on_a_textured_plane():
    rng = np.random.default_rng(3)
    texel = 0.002
    texture = cv2.GaussianBlur(rng.random((3000, 3000)).astype(np.float32), (0, 0), 3)
    z_plane = 3.0
    T1 = look_at(np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, z_plane]))
    T2 = look_at(np.array([0.4, 0.05, 0.1]), np.array([0.1, 0.0, z_plane]))
    img1 = render_plane(T1, texture, texel, z_plane)
    img2 = render_plane(T2, texture, texel, z_plane)

    for uv1 in (np.array([300.3, 250.6]), np.array([400.0, 180.25])):
        o1, d1 = pixel_ray(K, T1, uv1)
        X = o1 + (z_plane - o1[2]) / d1[2] * d1
        truth = project(K, T2, X[None])[0][0]
        uv2, score = epipolar_match(img1, img2, uv1, K, T1, K, T2)
        assert score > 0.95
        assert np.linalg.norm(uv2 - truth) < 0.3
        o2, d2 = pixel_ray(K, T2, uv2)
        got, _ = midpoint_triangulate(o1, d1, o2, d2)
        assert np.linalg.norm(got - X) < 0.01


def test_epipolar_search_fails_cleanly_when_the_template_leaves_the_image():
    img = np.zeros((480, 640), np.float32)
    T = np.eye(4)
    uv2, score = epipolar_match(img, img, np.array([3.0, 3.0]), K, T, K, T)
    assert np.isnan(uv2).all()
    assert score == -1.0


def test_epipolar_search_longer_than_one_remap_still_matches():
    # A 0.05 px step makes the segment's patches overflow cv2.remap's SHRT_MAX rows, as a wide
    # photo pair's long segment does at the normal step.
    rng = np.random.default_rng(3)
    texture = cv2.GaussianBlur(rng.random((3000, 3000)).astype(np.float32), (0, 0), 3)
    T1 = look_at(np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, 3.0]))
    T2 = look_at(np.array([0.4, 0.05, 0.1]), np.array([0.1, 0.0, 3.0]))
    img1, img2 = render_plane(T1, texture, 0.002, 3.0), render_plane(T2, texture, 0.002, 3.0)
    uv1 = np.array([300.3, 250.6])
    o1, d1 = pixel_ray(K, T1, uv1)
    truth = project(K, T2, (o1 + (3.0 - o1[2]) / d1[2] * d1)[None])[0][0]
    uv2, score = epipolar_match(img1, img2, uv1, K, T1, K, T2, step_px=0.05)
    assert score > 0.95
    assert np.linalg.norm(uv2 - truth) < 0.3
