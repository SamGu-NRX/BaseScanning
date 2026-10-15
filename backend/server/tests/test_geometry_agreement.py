"""pixel_ray_arkit matches the harness ray on a fixed set of pixels."""

from __future__ import annotations

import numpy as np

from server.common.geometry import pixel_ray_arkit
from server.eval.frame import ray_arkit


def test_pixel_ray_matches_eval_ray() -> None:
    pose = np.eye(4)
    angle = 0.4
    cosine, sine = float(np.cos(angle)), float(np.sin(angle))
    pose[:3, :3] = np.array([[cosine, 0.0, sine], [0.0, 1.0, 0.0], [-sine, 0.0, cosine]])
    intrinsics = (480.0, 470.0, 320.0, 240.0)
    pixels = np.random.default_rng(0).uniform([0.0, 0.0], [640.0, 480.0], size=(100, 2))
    for u, v in pixels:
        _origin, direction = pixel_ray_arkit(pose, intrinsics, np.array([u, v]))
        expected = np.asarray(ray_arkit(pose, intrinsics, u, v), dtype=float).reshape(3)
        np.testing.assert_allclose(direction, expected, atol=1e-9, rtol=0)
