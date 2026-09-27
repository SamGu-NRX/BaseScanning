import json

import cv2
import numpy as np
import pytest

from server.common.geometry import pixel_ray_arkit
from server.scale.apply import CameraRecord
from server.scale.contour import ContourSet, Ellipse, fit_meter_contours, label_contours
from server.scale.meter_table import MeterTable, TableMatch
from server.scale.reads_view import MeterIdentity
from server.scale.rim import _two_view, measure_cover

SEED = __import__("pathlib").Path(__file__).resolve().parent / "fixtures" / "meter_models.seed.v1.json"
COVER = 0.1598
RING = 0.1795
BASE = 0.1765
F = 3028.0
Z = 0.6


def _match() -> TableMatch:
    table = MeterTable.load(SEED)
    found = table.lookup(
        MeterIdentity(
            "Itron",
            "Itron CENTRON",
            "2S",
            "printed",
            0.9,
            True,
            ["s1"],
            "polycarbonate",
            "ring",
            True,
            True,
            None,
            "reads",
        )
    )
    assert isinstance(found, TableMatch)
    return found


def _pose_at(yaw_deg: float, distance: float = Z) -> np.ndarray:
    yaw = np.deg2rad(yaw_deg)
    pose = np.eye(4)
    pose[:3, :3] = np.array([[np.cos(yaw), 0, np.sin(yaw)], [0, 1, 0], [-np.sin(yaw), 0, np.cos(yaw)]])
    pose[:3, 3] = (np.sin(yaw) * distance, 0.0, np.cos(yaw) * distance)
    return pose


def _render(seed: int, angle_deg: float, diameters: list[float]):
    """Perspective projection of concentric circles, so the two views share one 3D circle."""

    rng = np.random.default_rng(seed)
    pose = _pose_at(angle_deg)
    jitter = rng.normal(0, 0.0001, size=2)
    image = np.full((1100, 1100), 20, np.uint8)
    centre = (550.0, 550.0)
    for diameter in diameters:
        angles = np.linspace(0, 2 * np.pi, 720, endpoint=False)
        circle = np.stack(
            [
                jitter[0] + (diameter / 2) * np.cos(angles),
                jitter[1] + (diameter / 2) * np.sin(angles),
                np.zeros(len(angles)),
            ],
            axis=1,
        )
        local = (pose[:3, :3].T @ (circle - pose[:3, 3]).T).T
        depth = np.maximum(-local[:, 2], 1e-6)
        pixels = np.stack([F * local[:, 0] / depth + 550, 550 - F * local[:, 1] / depth], axis=1)
        (centre_x, centre_y), (width, height), angle = cv2.fitEllipse(pixels.astype(np.float32))
        centre = (float(centre_x), float(centre_y))
        # OpenCV's angle belongs to the width axis, so the stroke uses that pair unswapped.
        _paint_ellipse(image, float(centre_x), float(centre_y), float(width) / 2, float(height) / 2, float(angle))
    box = [centre[0] - 560, centre[1] - 560, centre[0] + 560, centre[1] + 560]
    return image, box, pose


def _paint_ellipse(image: np.ndarray, cx: float, cy: float, axis_u: float, axis_v: float, theta: float) -> None:
    """Stroke the projected ellipse. A hard edge is what the contour fitter snaps to."""

    cv2.ellipse(
        image,
        (int(round(cx)), int(round(cy))),
        (max(1, int(round(axis_u))), max(1, int(round(axis_v)))),
        float(theta),
        0,
        360,
        240,
        1,
    )


def _camera(x: float, z: float, yaw_deg: float) -> CameraRecord:
    yaw = np.deg2rad(yaw_deg)
    pose = np.eye(4)
    pose[:3, :3] = np.array([[np.cos(yaw), 0, np.sin(yaw)], [0, 1, 0], [-np.sin(yaw), 0, np.cos(yaw)]])
    pose[:3, 3] = (x, 0.0, z)
    return CameraRecord("view", "view", pose, [F, F, 550, 550], 1100, 1100)


def _wall_annulus(semi: float, z_wall: float) -> np.ndarray:
    x_near = 1.2 * semi * z_wall / F
    x_far = 2.2 * semi * z_wall / F
    xs = np.concatenate([np.linspace(-x_far, -x_near, 4), np.linspace(x_near, x_far, 4)])
    ys = np.linspace(-0.04, 0.04, 3)
    return np.array([(x, y, -z_wall) for x in xs for y in ys])


def _projected_cover(camera: CameraRecord, radius: float) -> Ellipse:
    angles = np.linspace(0, 2 * np.pi, 72, endpoint=False)
    circle = np.stack([radius * np.cos(angles), radius * np.sin(angles), np.zeros(len(angles))], axis=1)
    pixels = []
    for point in circle:
        local = camera.pose[:3, :3].T @ (point - camera.pose[:3, 3])
        depth = -local[2]
        u = camera.intrinsics[0] * local[0] / depth + camera.intrinsics[2]
        v = camera.intrinsics[3] - camera.intrinsics[1] * local[1] / depth
        pixels.append((u, v))
    (centre_x, centre_y), (width, height), angle = cv2.fitEllipse(np.array(pixels, np.float32))
    major, minor, theta = float(width), float(height), float(angle)
    if minor > major:
        major, minor = minor, major
        theta += 90.0
    return Ellipse(float(centre_x), float(centre_y), major / 2, minor / 2, theta % 180.0, 1.0, 1.0)


@pytest.mark.parametrize("seed", range(20))
def test_three_contours_label_and_measure(seed: int):
    match = _match()
    head_image, box, head_pose = _render(seed, 6.5, [COVER, BASE, RING])
    oblique_image, oblique_box, oblique_pose = _render(seed + 100, 31.0, [COVER, BASE, RING])
    head = label_contours(fit_meter_contours(head_image, box, seed=seed), match, None)
    oblique = label_contours(fit_meter_contours(oblique_image, oblique_box, seed=seed), match, None)
    assert head.code is None and head.cover and head.ring and head.base
    assert oblique.code is None and oblique.cover and oblique.ring and oblique.base
    assert abs(np.degrees(np.arccos(head.cover.b / head.cover.a))) <= 30
    oblique_angle = abs(np.degrees(np.arccos(oblique.cover.b / oblique.cover.a)))
    assert 15 <= oblique_angle <= 50
    head_cam = CameraRecord("head", "head", head_pose, [F, F, 550, 550], 1100, 1100)
    oblique_cam = CameraRecord("oblique", "oblique", oblique_pose, [F, F, 550, 550], 1100, 1100)
    measured = measure_cover([(head_cam, head), (oblique_cam, oblique)], match, np.zeros((0, 3)), None)
    assert measured.code is None
    assert measured.method == "two_view"
    assert measured.scale == pytest.approx(1.0, rel=0.005)
    origin, direction = pixel_ray_arkit(head_cam.pose, head_cam.intrinsics, np.array([550.0, 550.0]))
    assert abs(float(np.dot(direction, np.array([0.0, 0.0, -1.0])))) > 0.9
    del origin


def test_one_view_rim_offset_is_within_one_percent():
    match = _match()
    rim = float(match.rim_offset_m)
    z_wall = 0.6
    semi = F * (COVER / 2) / (z_wall - rim)
    cover = Ellipse(550.0, 550.0, semi, semi * float(np.cos(np.deg2rad(6.5))), 0.0, 1.0, 1.0)
    contours = ContourSet(cover, None, None, False, None)
    camera = _camera(0.0, 0.0, 0.0)
    cloud = _wall_annulus(semi, z_wall)
    measured = measure_cover([(camera, contours)], match, np.zeros((0, 3)), {camera.image: cloud})
    assert measured.code is None
    assert measured.method == "one_view_rim_offset"
    assert measured.scale == pytest.approx(1.0, rel=0.01)


def test_one_view_plane_uses_the_wall_band_not_the_cover():
    match = _match()
    rim = float(match.rim_offset_m)
    z_wall = 0.6
    semi = F * (COVER / 2) / (z_wall - rim)
    cover = Ellipse(550.0, 550.0, semi, semi * float(np.cos(np.deg2rad(6.5))), 0.0, 1.0, 1.0)
    contours = ContourSet(cover, None, None, False, None)
    camera = _camera(0.0, 0.0, 0.0)
    wall = _wall_annulus(semi, z_wall)
    cover_depth = z_wall - rim
    x_in = 0.3 * semi * cover_depth / F
    xs = np.linspace(-x_in, x_in, 8)
    ys = np.linspace(-0.02, 0.02, 8)
    cover_tracks = np.array([(x, y, -cover_depth) for x in xs for y in ys])
    measured = measure_cover(
        [(camera, contours)], match, np.zeros((0, 3)), {camera.image: np.vstack([wall, cover_tracks])}
    )
    assert measured.code is None
    assert measured.scale == pytest.approx(1.0, rel=0.01)


def test_two_view_refinement_accepts_a_true_circle():
    head = _camera(0.0, Z, 0.0)
    yaw = np.deg2rad(31)
    oblique = _camera(np.sin(yaw) * Z, np.cos(yaw) * Z, 31.0)
    radius = COVER / 2
    measured = _two_view(
        (head, ContourSet(_projected_cover(head, radius), None, None, False, None)),
        (oblique, ContourSet(_projected_cover(oblique, radius), None, None, False, None)),
        COVER,
    )
    assert measured.code is None
    assert measured.rms_px <= 1.5
    assert measured.out_of_plane_m is not None
    assert measured.out_of_plane_m <= 0.001
    assert measured.scale == pytest.approx(1.0, rel=0.005)


def test_two_view_refinement_rejects_a_bad_pair():
    head = _camera(0.0, Z, 0.0)
    yaw = np.deg2rad(31)
    oblique = _camera(np.sin(yaw) * Z, np.cos(yaw) * Z, 31.0)
    radius = COVER / 2
    bad = _projected_cover(oblique, radius)
    bad.a *= 1.4
    bad.b *= 1.4
    measured = _two_view(
        (head, ContourSet(_projected_cover(head, radius), None, None, False, None)),
        (oblique, ContourSet(bad, None, None, False, None)),
        COVER,
    )
    assert measured.code == "scale_reference_views_disagree"
    assert measured.rms_px > 1.5


def test_ws04_csr_tracks_feed_one_view_and_walk_frame(tmp_path):
    """D10: compressed indptr/indices/point_ids expand into the meter paths."""

    from server.scale.packet_view import load_tracks
    from server.scale.params import ScaleParams
    from server.scale.run import _walk_frame

    match = _match()
    rim = float(match.rim_offset_m)
    z_wall = 0.6
    one_semi = F * (COVER / 2) / (z_wall - rim)
    walk_depth = 0.6
    walk_semi = F * (COVER / 2) / walk_depth
    one_points = _wall_annulus(one_semi, z_wall)
    walk_points = _wall_annulus(walk_semi, walk_depth)
    cloud = np.vstack([one_points, walk_points])
    poses = tmp_path / "poses"
    poses.mkdir()
    pose = np.eye(4).reshape(-1).tolist()
    cameras = [
        {"image": "view", "keyframe_id": "view", "pose": pose, "intrinsics": [F, F, 550, 550], "w": 1100, "h": 1100},
        {"image": "walk", "keyframe_id": "walk", "pose": pose, "intrinsics": [F, F, 550, 550], "w": 1100, "h": 1100},
    ]
    (poses / "cameras.json").write_text(json.dumps(cameras))
    _write_colour_ply(poses / "sparse.ply", cloud)
    n = len(cloud)
    indices = np.concatenate([np.zeros(len(one_points), np.int32), np.ones(len(walk_points), np.int32)])
    np.savez(
        poses / "tracks.npz",
        indptr=np.arange(n + 1, dtype=np.int64),
        indices=indices,
        point_ids=np.arange(n, dtype=np.int64),
    )
    tracks = load_tracks(poses)
    cover = Ellipse(550.0, 550.0, one_semi, one_semi * float(np.cos(np.deg2rad(6.5))), 0.0, 1.0, 1.0)
    measured = measure_cover(
        [(_camera(0.0, 0.0, 0.0), ContourSet(cover, None, None, False, None))],
        match,
        np.zeros((0, 3)),
        tracks,
    )
    assert measured.code is None
    assert measured.method == "one_view_rim_offset"
    assert measured.scale == pytest.approx(1.0, rel=0.01)

    image = np.full((1100, 1100), 20, np.uint8)
    _paint_ellipse(image, 550.0, 550.0, walk_semi, walk_semi * float(np.cos(np.deg2rad(6.5))), 0.0)
    cv2.imwrite(str(tmp_path / "walk.jpg"), image)
    walk = CameraRecord("walk", "walk", np.eye(4), [F, F, 550.0, 550.0], 1100, 1100)
    packet = {
        "stills": [{"id": "walk", "img": "walk.jpg"}],
        "scaleReference": {"meterCloseUp": {"captured": True, "still": "walk", "arkitDistanceM": walk_depth}},
    }
    walked = _walk_frame(
        packet,
        tmp_path,
        [walk],
        {"walk": [0.0, 0.0, 1100.0, 1100.0]},
        tracks,
        match,
        ScaleParams(),
        0.03,
    )
    assert walked is not None
    assert walked.method == "walk_frame"
    assert walked.scale == pytest.approx(1.0, rel=0.02)


def _write_colour_ply(path, xyz: np.ndarray) -> None:
    cloud = np.asarray(xyz, np.float32)
    n = len(cloud)
    dtype = np.dtype(
        [
            ("x", "<f4"),
            ("y", "<f4"),
            ("z", "<f4"),
            ("red", "u1"),
            ("green", "u1"),
            ("blue", "u1"),
            ("n_views", "<i4"),
        ]
    )
    arr = np.empty(n, dtype=dtype)
    arr["x"], arr["y"], arr["z"] = cloud[:, 0], cloud[:, 1], cloud[:, 2]
    arr["red"] = arr["green"] = arr["blue"] = 0
    arr["n_views"] = 2
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {n}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "property int32 n_views\nend_header\n"
    )
    path.write_bytes(header.encode("ascii") + arr.tobytes())


def test_walk_frame_measures_a_lone_cover_from_the_wall_plane(tmp_path):
    from server.scale.params import ScaleParams
    from server.scale.run import _walk_frame

    match = _match()
    depth = 0.6
    semi = F * (COVER / 2) / depth
    image = np.full((1100, 1100), 20, np.uint8)
    _paint_ellipse(image, 550.0, 550.0, semi, semi * float(np.cos(np.deg2rad(6.5))), 0.0)
    cv2.imwrite(str(tmp_path / "walk.jpg"), image)
    camera = CameraRecord("walk", "walk", np.eye(4), [F, F, 550.0, 550.0], 1100, 1100)
    packet = {
        "stills": [{"id": "walk", "img": "walk.jpg"}],
        "scaleReference": {"meterCloseUp": {"captured": True, "still": "walk", "arkitDistanceM": depth}},
    }
    measured = _walk_frame(
        packet,
        tmp_path,
        [camera],
        {"walk": [0.0, 0.0, 1100.0, 1100.0]},
        {"walk": _wall_annulus(semi, depth)},
        match,
        ScaleParams(),
        0.03,
    )
    assert measured is not None
    assert measured.method == "walk_frame"
    assert measured.rel_sd == pytest.approx(0.03)
    assert measured.scale == pytest.approx(1.0, rel=0.02)


def test_ring_only_is_ambiguous_until_the_size_test():
    match = _match()
    image, box, _pose = _render(1, 6.5, [RING])
    ellipses = fit_meter_contours(image, box, seed=1)
    ambiguous = label_contours(ellipses, match, None)
    assert ambiguous.code == "meter_contour_ambiguous"
    # Base is 1.7% from the ring, inside a 6% window, so a modern ARKit SD does not resolve it.
    still = label_contours(ellipses, match, RING, arkit_rel_sd=0.03)
    assert still.code == "meter_contour_ambiguous" and still.lone
    resolved = label_contours(ellipses, match, RING, arkit_rel_sd=0.005)
    assert resolved.code is None and resolved.ring is not None and resolved.lone


def test_major_axis_angle_matches_the_long_axis():
    from server.scale.contour import _from_fit, _residuals

    theta = np.deg2rad(25.0)
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    points = []
    for turn in np.linspace(0, 2 * np.pi, 90, endpoint=False):
        world = rotation @ np.array([50.0 * np.cos(turn), 18.0 * np.sin(turn)]) + np.array([120.0, 90.0])
        points.append((float(world[0]), float(world[1])))
    fitted = cv2.fitEllipse(np.asarray(points, np.float32).reshape(-1, 1, 2))
    ellipse = _from_fit(fitted, 1.0, 1.0)
    assert float(np.median(_residuals(points, ellipse))) < 1.5


def test_second_closeup_is_measured_when_the_first_is_ambiguous(monkeypatch, tmp_path):
    import importlib

    from server.scale.contour import ContourSet, Ellipse
    from server.scale.rim import CoverMeasurement

    scale_run = importlib.import_module("server.scale.run")
    _measure_closeups = scale_run._measure_closeups

    pose = np.eye(4)
    first = CameraRecord("close", None, pose, [500.0, 500.0, 250.0, 250.0], 100, 100)
    second = CameraRecord("oblique", None, pose, [500.0, 500.0, 250.0, 250.0], 100, 100)
    seen = {"n": 0}

    def _label(*_args, **_kwargs):
        seen["n"] += 1
        if seen["n"] == 1:
            return ContourSet(None, None, None, True, "meter_contour_ambiguous")
        cover = Ellipse(10, 10, 40, 30, 0, 1, 1)
        return ContourSet(cover, None, None, False, None)

    monkeypatch.setattr(scale_run, "_load_gray", lambda *_a, **_k: np.zeros((20, 20), np.uint8))
    monkeypatch.setattr(scale_run, "fit_meter_contours", lambda *_a, **_k: [Ellipse(0, 0, 1, 1, 0, 1, 1)])
    monkeypatch.setattr(scale_run, "label_contours", _label)
    monkeypatch.setattr(scale_run, "write_overlay", lambda *_a, **_k: None)
    monkeypatch.setattr(
        scale_run,
        "measure_cover",
        lambda views, *_a, **_k: CoverMeasurement(
            "one_view_rim_offset", 1.0, 0.01, 0.0, np.zeros(3), np.zeros(3), [], [], 0.0, None, None, [], False
        ),
    )
    measured, images = _measure_closeups(
        {},
        tmp_path,
        [(first, "close"), (second, "oblique")],
        _match(),
        np.zeros((0, 3)),
        {},
        None,
        tmp_path,
        type("P", (), {"seed": 0})(),
        0.03,
    )
    assert measured is not None and measured.code is None
    assert images == ["close", "oblique"]
    assert seen["n"] == 2
