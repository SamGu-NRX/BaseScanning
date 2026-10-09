import numpy as np
import pytest

from autodetect import electro
from autodetect.electro import Photo, WallFrame, fit_vertical_plane

K = np.array([100.0, 100.0, 50.0, 40.0])  # fx, fy, cx, cy for a 100x80 photo


def make_photo(pose, depth):
    return Photo("p0", 100, 80, K, pose, depth)


def rotated_pose(degrees, translation):
    th = np.radians(degrees)
    pose = np.eye(4)
    pose[:3, :3] = np.array([
        [np.cos(th), 0.0, np.sin(th)],
        [0.0, 1.0, 0.0],
        [-np.sin(th), 0.0, np.cos(th)],
    ])
    pose[:3, 3] = translation
    return pose


def fake_manifest():
    return {
        "session": {"id": "eth3d-electro-9257-9264"},
        "photos": [{"id": f"p{k}"} for k in range(8)],
    }


def fake_colmap_poses():
    t = np.eye(4)
    t[:3, 3] = [1.0, 2.0, 3.0]
    return {f"DSC_{9257 + k}.JPG": t.copy() for k in range(8)}


def test_ray_directions_are_unit_and_start_at_the_pose_origin():
    p = make_photo(rotated_pose(20.0, [0.3, 1.2, 4.0]), np.zeros((4, 4), np.float32))
    o, d = p.ray(np.array([0.0, 50.0, 99.0]), np.array([0.0, 40.0, 79.0]))
    assert np.allclose(o, [0.3, 1.2, 4.0])
    assert np.allclose(np.linalg.norm(d, axis=-1), 1.0)


def test_project_inverts_ray_for_points_in_front_of_the_camera():
    p = make_photo(rotated_pose(20.0, [0.3, 1.2, 4.0]), np.zeros((4, 4), np.float32))
    u = np.array([0.0, 50.0, 99.0, 25.0])
    v = np.array([0.0, 40.0, 79.0, 10.0])
    o, d = p.ray(u, v)
    assert np.allclose(p.project(o + 2.5 * d), np.stack([u / 100.0, v / 80.0], -1))


def test_depth_points_lie_on_the_depth_plane_and_round_trip_through_project():
    p = make_photo(np.eye(4), np.full((8, 6), 2.0, np.float32))
    pts = p.depth_points(0.0, 0.0, 1.0, 1.0)
    assert pts.shape == (48, 3)
    assert np.allclose(pts[:, 2], -2.0)  # identity pose: depth 2 m along -z is meter z = -2
    rr, cc = np.mgrid[0:8, 0:6]
    centres = np.stack([(cc.ravel() + 0.5) / 6, (rr.ravel() + 0.5) / 8], -1)
    assert np.allclose(p.project(pts), centres)


def test_depth_points_skip_zero_depth_and_clip_to_the_box():
    depth = np.full((8, 6), 2.0, np.float32)
    depth[2, 2] = 0.0
    p = make_photo(np.eye(4), depth)
    pts = p.depth_points(0.25, 0.0, 0.75, 1.0)
    assert pts.shape == (4 * 8 - 1, 3)  # columns 1 to 4, the zero-depth pixel dropped
    uv = p.project(pts)
    assert uv[:, 0].min() >= 0.25 and uv[:, 0].max() <= 0.75
    assert p.depth_points(1.1, 0.0, 1.2, 1.0).shape == (0, 3)


def test_wallframe_from_normal_flattens_tilt_and_picks_along():
    f = WallFrame.from_normal(np.array([1.0, 2.0, 3.0]), np.array([0.3, 5.0, 0.4]))
    assert np.allclose(f.origin, [1.0, 2.0, 3.0])
    assert np.allclose(f.normal, [0.6, 0.0, 0.8])
    assert np.allclose(f.along, [0.8, 0.0, -0.6])


def test_coords_measure_from_the_origin_and_the_ground(monkeypatch):
    monkeypatch.setattr(electro, "ground_y", lambda: 1.5)
    f = WallFrame.from_normal(np.array([1.0, 2.0, 3.0]), np.array([0.0, 0.0, 1.0]))
    p = f.origin + 2.0 * f.along + 3.0 * f.normal + np.array([0.0, 4.0, 0.0])
    # height uses the point's absolute y, not the offset from the frame origin at y = 2
    assert np.allclose(f.coords(p), [2.0, 4.5, 3.0])


def test_intersect_hits_the_plane():
    f = WallFrame.from_normal(np.zeros(3), np.array([0.0, 0.0, 1.0]))
    hits = f.intersect(
        np.array([2.0, 1.0, 5.0]),
        np.array([[-2.0, -1.0, -5.0], [-1.2, -0.6, -5.0]]),
    )
    assert np.allclose(hits[0], [0.0, 0.0, 0.0])
    assert np.allclose(hits[1], [0.8, 0.4, 0.0])


def test_intersect_of_a_parallel_ray_is_not_finite():
    f = WallFrame.from_normal(np.zeros(3), np.array([0.0, 0.0, 1.0]))
    hit = f.intersect(np.array([0.0, 0.0, 2.0]), np.array([[1.0, 0.0, 0.0]]))
    assert not np.isfinite(hit).any()


def test_fit_vertical_plane_recovers_a_synthetic_wall():
    th = np.radians(20.0)
    n_true = np.array([np.sin(th), 0.0, np.cos(th)])
    along = np.array([np.cos(th), 0.0, -np.sin(th)])
    origin = np.array([0.5, 0.0, 0.0])
    aa, bb = np.meshgrid(np.linspace(-1.0, 1.0, 10), np.linspace(0.0, 2.0, 8))
    wall = origin + aa.ravel()[:, None] * along + bb.ravel()[:, None] * np.array([0.0, 1.0, 0.0])
    outliers = np.array([[0.0, 0.1, 0.0], [1.0, 0.2, 0.0], [0.2, 0.3, 1.0], [0.9, 0.4, 1.0]])
    toward = origin + 5 * n_true

    frame, frac = fit_vertical_plane(np.vstack([wall, outliers]), toward=toward)

    angle = np.degrees(np.arccos(np.clip(frame.normal @ n_true, -1.0, 1.0)))
    assert angle < 1.0
    assert frame.normal[1] == 0.0  # from_normal forces the normal vertical
    assert abs((frame.origin - origin) @ n_true) < 0.01  # origin sits on the wall
    assert frac == pytest.approx(80 / 84, abs=0.01)
    assert (toward - frame.origin) @ frame.normal > 0  # normal faces the camera


def test_fit_vertical_plane_faces_the_normal_toward_the_camera():
    pts = np.array([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0]])
    frame, _ = fit_vertical_plane(pts, toward=np.array([0.0, 0.0, -5.0]))
    assert np.allclose(frame.normal, [0.0, 0.0, -1.0])


def test_fit_vertical_plane_raises_without_a_vertical_candidate():
    pts = np.array([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 2.0, 0.0]])  # collinear
    with pytest.raises(ValueError, match="no vertical plane"):
        fit_vertical_plane(pts, toward=np.array([0.0, 0.0, 5.0]))


def test_meter_from_scan_composes_pose_diag_and_colmap(monkeypatch):
    monkeypatch.setattr(electro, "manifest", fake_manifest)
    monkeypatch.setattr(electro, "_colmap_poses", fake_colmap_poses)
    pose = rotated_pose(10.0, [0.1, 0.2, 0.3])
    monkeypatch.setattr(
        electro, "photos", lambda: {f"p{k}": make_photo(pose, np.zeros((1, 1), np.float32)) for k in range(8)}
    )

    expected = pose @ np.diag([1.0, -1.0, -1.0, 1.0]) @ fake_colmap_poses()["DSC_9257.JPG"]

    assert np.allclose(electro.meter_from_scan(), expected)


def test_meter_from_scan_rejects_photos_that_disagree(monkeypatch):
    monkeypatch.setattr(electro, "manifest", fake_manifest)
    monkeypatch.setattr(electro, "_colmap_poses", fake_colmap_poses)
    pose = rotated_pose(10.0, [0.1, 0.2, 0.3])
    shifted = pose.copy()
    shifted[:3, 3] += [0.002, 0.0, 0.0]  # 2 mm off, above the 1 mm tolerance
    photos = {f"p{k}": make_photo(pose, np.zeros((1, 1), np.float32)) for k in range(8)}
    photos["p5"] = make_photo(shifted, np.zeros((1, 1), np.float32))
    monkeypatch.setattr(electro, "photos", lambda: photos)

    with pytest.raises(ValueError, match="disagree"):
        electro.meter_from_scan()


def test_scan_transforms_the_scan_and_caches_it_in_data(monkeypatch, tmp_path):
    evals = tmp_path / "evals"
    evals.mkdir()
    np.save(evals / "scan_points_10mm.npy", np.array([[1.0, 0.0, 0.0], [0.0, 2.0, 0.0]]))
    data = tmp_path / "data"
    data.mkdir()  # scan() writes the cache here and never creates the directory
    monkeypatch.setattr(electro, "EVALS", evals)
    monkeypatch.setattr(electro, "DATA", data)
    monkeypatch.setattr(electro, "manifest", fake_manifest)
    monkeypatch.setattr(electro, "_colmap_poses", fake_colmap_poses)
    pose = rotated_pose(10.0, [0.1, 0.2, 0.3])
    monkeypatch.setattr(
        electro, "photos", lambda: {f"p{k}": make_photo(pose, np.zeros((1, 1), np.float32)) for k in range(8)}
    )

    q = electro.scan.__wrapped__()  # __wrapped__ skips the functools.cache

    t = pose @ np.diag([1.0, -1.0, -1.0, 1.0]) @ fake_colmap_poses()["DSC_9257.JPG"]
    expected = np.array([[1.0, 0.0, 0.0], [0.0, 2.0, 0.0]]) @ t[:3, :3].T + t[:3, 3]
    assert q.dtype == np.float32
    assert np.allclose(q, expected, atol=1e-6)
    assert (tmp_path / "data" / "scan_meter_frame.npy").exists()

    def fail():
        raise AssertionError("cache miss")

    monkeypatch.setattr(electro, "_colmap_poses", fail)
    assert np.allclose(electro.scan.__wrapped__(), expected, atol=1e-6)  # reloaded from the file


def test_photos_decode_column_major_poses_and_depth(monkeypatch, tmp_path):
    packet = tmp_path / "packet"
    packet.mkdir()
    (packet / "d0.bin").write_bytes(np.array([1.0, 2.0, 0.0, 3.0], "<f4").tobytes())
    monkeypatch.setattr(electro, "ELECTRO", packet)
    monkeypatch.setattr(electro, "manifest", lambda: {
        "photos": [{
            "id": "d0",
            "width": 4,
            "height": 2,
            "intrinsics": [10.0, 10.0, 2.0, 1.0],
            # column-major 4x4: column 3 holds the translation
            "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0.5, 1.5, 2.5, 1.0],
            "depth": {"map": {"path": "d0.bin"}, "height": 2, "width": 2},
        }],
    })

    p = electro.photos.__wrapped__()["d0"]

    assert p.W == 4 and p.H == 2
    assert np.allclose(p.K, [10.0, 10.0, 2.0, 1.0])
    assert np.allclose(p.pose[:3, :3], np.eye(3))
    assert np.allclose(p.pose[:3, 3], [0.5, 1.5, 2.5])
    assert p.depth.dtype == np.float32 and np.allclose(p.depth, [[1.0, 2.0], [0.0, 3.0]])


def test_manifest_reads_the_packet_manifest(monkeypatch, tmp_path):
    packet = tmp_path / "packet"
    packet.mkdir()
    (packet / "manifest.json").write_text('{"a": 1}')
    monkeypatch.setattr(electro, "ELECTRO", packet)
    assert electro.manifest.__wrapped__() == {"a": 1}


def test_ground_y_reads_the_session_anchor(monkeypatch):
    monkeypatch.setattr(electro, "manifest", lambda: {"session": {"meter_anchor": {"ground_y_m": 1.23}}})
    assert electro.ground_y() == 1.23


def test_ft_is_feet_per_meter():
    assert electro.FT == pytest.approx(1 / 0.3048, abs=1e-8)
