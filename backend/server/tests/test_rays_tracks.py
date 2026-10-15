"""rays_from_tracks stays correct and does not scan cameras × points."""

from __future__ import annotations

import time

import numpy as np

from server.common.rays import rays_from_tracks


class _Cam:
    def __init__(self, x: float) -> None:
        self.pose = np.eye(4)
        self.pose[0, 3] = x


def test_point_is_listed_only_on_its_cameras():
    cameras = [_Cam(0.0), _Cam(1.0), _Cam(2.0)]
    points = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 1.0], [2.0, 0.0, 1.0]])
    tracks = [np.array([0, 0]), np.array([1]), np.zeros(0, np.int32)]
    src = np.array([0, 2, 1])
    rays = rays_from_tracks(cameras, points, tracks, src)
    assert rays[0][1].shape == (1, 3)
    assert rays[0][1][0, 0] == 0.0
    assert rays[0][2] is None
    assert rays[1][1][0, 0] == 1.0
    assert rays[1][2] is not None
    assert bool(rays[1][2][0]) is True
    assert rays[2][1].shape == (0, 3)
    assert rays[2][2] is None
    assert np.allclose(rays[2][0], [2.0, 0.0, 0.0])


def test_out_of_range_camera_index_is_ignored():
    rays = rays_from_tracks([_Cam(0.0)], np.zeros((1, 3)), [np.array([-1, 4])], np.array([1]))
    assert rays[0][1].shape == (0, 3)


def test_empty_tracks_stay_fast():
    n_cams, n_points = 100, 20_000
    cameras = [_Cam(float(i)) for i in range(n_cams)]
    points = np.zeros((n_points, 3))
    tracks = [np.zeros(0, np.int32) for _ in range(n_points)]
    src = np.zeros(n_points, np.int32)
    started = time.perf_counter()
    rays = rays_from_tracks(cameras, points, tracks, src)
    elapsed = time.perf_counter() - started
    assert len(rays) == n_cams
    assert all(row[1].shape == (0, 3) and row[2] is None for row in rays)
    assert elapsed < 0.5
