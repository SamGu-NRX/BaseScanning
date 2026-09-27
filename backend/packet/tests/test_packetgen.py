import copy
import json
import math
from pathlib import Path

import jsonschema
import numpy as np
import pytest

from packetgen.align import align
from packetgen.conventions import (
    DEVICE_FROM_CAMERA,
    column_major,
    cv_to_arkit_pose,
    from_column_major,
    heading_deg_of_vector,
    pixel_ray_arkit,
    portrait_to_landscape,
    project_arkit,
    rodrigues,
)
from packetgen.noise import apply_drift, continuous_walk_order, draw_modern_walk, load_profiles, rigid_residuals
from packetgen.sensors import enu_from_arkit, nwu_from_arkit_matrix, plane_anchors
from packetgen.sources import Recon, View

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "capture-packet.schema.json").read_text())


def random_pose(rng):
    T = np.eye(4)
    T[:3, :3] = rodrigues(rng.normal(0, 1, 3))
    T[:3, 3] = rng.normal(0, 3, 3)
    return T


def project_cv(T_cv, K, X):
    fx, fy, cx, cy = K
    Xc = (X - T_cv[:3, 3]) @ T_cv[:3, :3]
    return np.stack([fx * Xc[:, 0] / Xc[:, 2] + cx, fy * Xc[:, 1] / Xc[:, 2] + cy], axis=1), Xc[:, 2]


def test_arkit_projection_matches_opencv():
    rng = np.random.default_rng(1)
    T_cv = random_pose(rng)
    K = (1400.0, 1410.0, 955.0, 725.0)
    X = T_cv[:3, 3] + (T_cv[:3, :3] @ np.column_stack([rng.normal(0, 1, 50), rng.normal(0, 1, 50), rng.uniform(2, 9, 50)]).T).T
    uv_cv, z_cv = project_cv(T_cv, K, X)
    uv_ar, z_ar = project_arkit(cv_to_arkit_pose(T_cv), K, X)
    assert np.allclose(uv_cv, uv_ar) and np.allclose(z_cv, z_ar)


def test_ray_through_projection_hits_point():
    rng = np.random.default_rng(2)
    T = cv_to_arkit_pose(random_pose(rng))
    K = (1442.0, 1442.0, 959.6, 719.4)
    X = T[:3, 3] + T[:3, :3] @ np.array([0.3, -0.2, -4.0])
    uv, _ = project_arkit(T, K, X[None])
    o, d = pixel_ray_arkit(T, K, uv[0])
    assert np.allclose(o + d * np.linalg.norm(X - o), X, atol=1e-9)


def test_portrait_to_landscape_pixels():
    rng = np.random.default_rng(3)
    T_p = random_pose(rng)
    Wp, Hp = 1440, 1920
    K_p = (1450.0, 1450.0, 721.0, 958.0)
    X = T_p[:3, 3] + (T_p[:3, :3] @ np.column_stack([rng.normal(0, 1, 20), rng.normal(0, 1, 20), rng.uniform(2, 6, 20)]).T).T
    uv_p, _ = project_cv(T_p, K_p, X)
    T_l, K_l = portrait_to_landscape(T_p, K_p, Wp)
    uv_l, _ = project_cv(T_l, K_l, X)
    assert np.allclose(uv_l[:, 0], uv_p[:, 1]) and np.allclose(uv_l[:, 1], Wp - uv_p[:, 0])


def test_column_major_roundtrip_and_layout():
    T = np.arange(16, dtype=float).reshape(4, 4)
    cm = column_major(T)
    assert cm[:4] == [0.0, 4.0, 8.0, 12.0]
    assert np.array_equal(from_column_major(cm), T)


def test_device_camera_axes():
    # Upright portrait phone looking along world -z: camera x = world down, camera y = world +x.
    R = np.column_stack([[0, -1, 0], [1, 0, 0], [0, 0, 1]]).astype(float)
    world_from_device = R @ DEVICE_FROM_CAMERA.T
    assert np.allclose(world_from_device, np.eye(3))


def test_heading_and_geodetic_frames():
    assert heading_deg_of_vector(np.array([0, 0, -1.0]), 30.0) == pytest.approx(30.0)
    assert heading_deg_of_vector(np.array([1.0, 0, 0]), 30.0) == pytest.approx(120.0)
    e, n, u = enu_from_arkit(np.array([1.0, 0.0, 0.0]), 0.0)
    assert (e, n) == pytest.approx((1.0, 0.0))
    assert np.allclose(nwu_from_arkit_matrix(0.0) @ [1.0, 0, 0], [0, -1, 0])  # east is -y (west) in NWU
    assert np.allclose(nwu_from_arkit_matrix(90.0) @ [0, 0, -1.0], [0, -1, 0])  # -z facing east


def straight_walk(n=60, step=0.5):
    c = np.column_stack([np.arange(n) * step, np.zeros(n), np.zeros(n)])
    R = np.repeat(np.column_stack([[0, -1, 0], [1, 0, 0], [0, 0, 1]]).astype(float)[None], n, axis=0)
    return R, c


def test_drift_zero_noise_is_identity_scale_only():
    prof = copy.deepcopy(load_profiles()["lidar_modern"])
    for a in ("jitter_pos_m", "jitter_rot_deg", "rw_pos_h_m_per_sqrt_m", "rw_pos_v_m_per_sqrt_m", "rw_yaw_deg_per_sqrt_m", "session_sigma_lognormal"):
        setattr(prof, a, 0.0)
    prof.scale = [type(prof.scale[0])(1.0, -0.05, 0.0)]
    R, c = straight_walk()
    R_o, c_o, ep, draw = apply_drift(R, c, prof, np.random.default_rng(0), reset_index=None)
    assert np.allclose(R_o, R)
    assert np.allclose(c_o, c * 0.95)
    assert (ep == 0).all()


def test_reset_starts_new_epoch_at_origin():
    prof = load_profiles()["nonlidar_2018"]
    R, c = straight_walk()
    _, c_o, ep, draw = apply_drift(R, c, prof, np.random.default_rng(0), reset_index=30)
    assert ep[29] == 0 and ep[30] == 1 and draw.reset_index == 30
    assert np.linalg.norm(c_o[30]) < 0.05


def test_profiles_match_pr12_targets():
    p = load_profiles()["lidar_modern"].evidence
    for ft, (m, p90) in p["target_in"].items():
        am, ap = p["achieved_in"][ft]
        assert abs(am / m - 1) < 0.25 and abs(ap / p90 - 1) < 0.25


def synthetic_scene(rng, R_world):
    """Wall (x = 5) and ground (y = -1.4) points, cameras walking along the wall, in a world
    rotated by R_world so 'up' is unknown to the solver."""
    wall = np.column_stack([np.full(4000, 5.0), rng.uniform(-1.4, 2.5, 4000), rng.uniform(-6, 6, 4000)])
    wall2 = np.column_stack([rng.uniform(-3, 5, 3000), rng.uniform(-1.4, 2.5, 3000), np.full(3000, -6.0)])
    ground = np.column_stack([rng.uniform(-3, 5, 4000), np.full(4000, -1.4), rng.uniform(-6, 6, 4000)])
    P = np.vstack([wall, wall2, ground]) + rng.normal(0, 0.005, (11000, 3))
    views = []
    for i in range(12):
        # OpenCV camera looking at +x (the wall), pitched up 15 degrees (y down in OpenCV).
        pitch = math.radians(15)
        z = np.array([math.cos(pitch), math.sin(pitch), 0.0])
        y = np.array([math.sin(pitch), -math.cos(pitch), 0.0])
        x = np.cross(y, z)
        T = np.eye(4)
        T[:3, :3] = R_world @ np.column_stack([x, y, z])
        T[:3, 3] = R_world @ np.array([0.0, 0.0, -4 + i * 0.7])
        views.append(View(f"f{i:02d}.jpg", Path("x"), 1920, 1440, (1400, 1400, 960, 720), T))
    ids = np.arange(len(P))
    return Recon(views, ids, (R_world @ P.T).T, np.zeros((len(P), 3), np.uint8), metric=False, source="test",
                 up_hint=R_world @ np.array([0, 1.0, 0]))


def test_gravity_from_planes_ignores_camera_pitch():
    rng = np.random.default_rng(5)
    R_world = rodrigues(np.array([0.4, -0.9, 0.3]))
    rec = synthetic_scene(rng, R_world)
    a = align(rec, rng)
    assert a.up_vs_hint_deg < 0.5
    assert a.up_vs_cameras_deg > 10
    assert a.camera_height_m == pytest.approx(1.40, abs=1e-6)


def test_vertical_planes_detected_with_lidar():
    rng = np.random.default_rng(6)
    rec = synthetic_scene(rng, np.eye(3))
    a = align(rec, rng)
    P = a.point(rec.points)
    T = [a.pose(v.T_cv) for v in rec.views]
    cams = np.array([t[:3, 3] for t in T])
    planes = plane_anchors(P, cams, T, T, np.zeros(len(T), int), [1.0], load_profiles()["lidar_modern"], True, rng)
    kinds = sorted(p["alignment"] for p in planes)
    assert "vertical" in kinds and "horizontal" in kinds
    wall = next(p for p in planes if p["alignment"] == "vertical" and p["planeExtent"]["width"] > 8)
    normal = wall["transform"][:3, 1]
    to_cam = cams.mean(0) - wall["transform"][:3, 3]
    assert normal @ to_cam > 0  # wall normal faces the camera side, as ARKit reports it


@pytest.mark.parametrize("name", ["example-packet.json", "example-packet-photo-only.json"])
def test_examples_validate(name):
    jsonschema.validate(json.loads((ROOT / "schema" / name).read_text()), SCHEMA, cls=jsonschema.Draft202012Validator)


def test_walk_order_does_not_integrate_a_filename_jump():
    """A 30 m filename jump in the middle of a 1 m walk must not set the drift."""
    prof = load_profiles()["nonlidar_modern"]
    n = 40
    z = np.arange(n, dtype=float)
    z[20] = 50.0  # filename order teleports here, then comes back
    c = np.column_stack([np.zeros(n), np.zeros(n), z])
    R = np.repeat(np.eye(3)[None], n, axis=0)
    seed = 7
    _, capture, _, _ = apply_drift(R, c, prof, np.random.default_rng([seed, 2]))
    _, walked, _, draw, _, offset = draw_modern_walk(R, c, prof, seed, 1)
    capture_rms = float(np.sqrt(np.mean(rigid_residuals(capture, c) ** 2)))
    walk_rms = float(np.sqrt(np.mean(rigid_residuals(walked, c) ** 2)))
    assert continuous_walk_order(c)[0] == 0
    assert walk_rms < capture_rms
    assert 0.065 <= walk_rms <= 0.22
    assert rigid_residuals(walked, c).max() <= 1.0
    assert draw.reset_index is None
    assert offset >= 0


def test_schema_rejects_missing_pose_and_bad_tier():
    pk = json.loads((ROOT / "schema" / "example-packet.json").read_text())
    bad = copy.deepcopy(pk)
    del bad["keyframes"][0]["pose"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA, cls=jsonschema.Draft202012Validator)
    bad = copy.deepcopy(pk)
    bad["session"]["tier"] = "photo_only"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA, cls=jsonschema.Draft202012Validator)
