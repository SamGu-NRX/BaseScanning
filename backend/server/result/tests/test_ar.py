"""ARKit frame mapping, the round trip, and camera poses."""

import numpy as np

from server.result import ar
from server.result.harness import frame as frame_mod
from server.result.tests.support import scene_dict, similarity


def test_roundtrip_similarities():
    rng = np.random.default_rng(0)
    points = rng.normal(size=(1000, 3))
    worst = 0.0
    for _ in range(5):
        scale = float(rng.uniform(0.9, 1.1))
        yaw = float(rng.uniform(-0.5, 0.5))
        c, s = np.cos(yaw), np.sin(yaw)
        pose = np.eye(4)
        pose[:3, :3] = scale * np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])
        pose[:3, 3] = rng.normal(size=3)
        inverse = np.linalg.inv(pose)
        back = frame_mod.apply(pose, frame_mod.apply(inverse, points))
        worst = max(worst, float(np.linalg.norm(back - points, axis=1).max()))
    assert worst < 1e-9


def test_source_frame_spellings():
    pose = frame_mod.mat4_to_list(np.eye(4))
    named = ar.scene_to_arkit({"source_frame": "arkit_world/e1", "from_source": pose})
    colon = ar.scene_to_arkit({"source_frame": "arkit_world:e9", "from_source": pose})
    bare = ar.scene_to_arkit({"source_frame": "arkit_world", "from_source": pose})
    other = ar.scene_to_arkit({"source_frame": "eth3d_world", "from_source": pose})
    assert named is not None and colon is not None and bare is not None
    assert other is None
    assert ar.epoch_id({"source_frame": "arkit_world/e1"}, None) == "e1"
    assert ar.epoch_id({"source_frame": "arkit_world:e9"}, None) == "e9"
    assert ar.epoch_id({"source_frame": "arkit_world"}, ["e1"]) == "e1"
    assert ar.epoch_id({"source_frame": "arkit_world"}, ["e1", "e2"]) is None
    assert ar.epoch_id({"source_frame": "eth3d_world"}, ["e1"]) is None


def test_known_similarity_inverts():
    source = similarity()
    inverse = ar.scene_to_arkit({"source_frame": "arkit_world/e1", "from_source": frame_mod.mat4_to_list(source)})
    assert inverse is not None
    assert np.allclose(inverse @ source, np.eye(4), atol=1e-5)


def _pose_checks(view: dict, scene: dict, *, out_m: float, height: float):
    pose = ar.camera_pose_for_view(view, scene)
    rotation = pose[:3, :3]
    camera = pose[:3, 3]
    assert abs(float(np.linalg.det(rotation)) - 1.0) < 1e-9
    assert np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-9)
    assert abs(float(camera[1]) - height) < 0.05
    assert abs(float(camera[2]) - out_m) < 0.05
    _surface, polygon = ar.region_polygon_scene(view, scene)
    centre = polygon.mean(axis=0)
    forward = -rotation[:, 2]
    toward = centre - camera
    toward = toward / np.linalg.norm(toward)
    angle = np.degrees(np.arccos(np.clip(float(np.dot(forward, toward)), -1.0, 1.0)))
    assert angle < 5.0


def test_wall_and_ground_camera_poses():
    scene = scene_dict(source_frame="arkit_world/e1", with_keyframe=False)
    wall = {"id": "vn-wall", "kind": "band", "band": "wall", "wall_id": "w1", "s_m": [-1.0, 1.0]}
    ground = {
        "id": "vn-ground",
        "kind": "band",
        "band": "ground",
        "wall_id": "w1",
        "s_m": [-0.5, 0.5],
        "out_m": 1.0,
    }
    _pose_checks(wall, scene, out_m=3.0, height=1.4)
    _pose_checks(ground, scene, out_m=1.0, height=1.3)
    assert ar.region_polygon_scene(wall, scene)[0] == "wall"
    assert ar.region_polygon_scene(ground, scene)[0] == "ground"


def test_reference_crop_when_frame_is_not_arkit():
    scene = scene_dict(source_frame="eth3d_world", with_keyframe=True)
    wall = {"id": "vn-wall", "kind": "band", "band": "wall", "wall_id": "w1", "s_m": [-1.0, 1.0]}
    _surface, polygon = ar.region_polygon_scene(wall, scene)
    crop = ar.best_reference_crop(polygon, scene)
    assert crop is not None
    assert crop["image"] == "k01.jpg"
    assert len(crop["boxPx"]) == 4
