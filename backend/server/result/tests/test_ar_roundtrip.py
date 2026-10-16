"""The proposed box lands where the phone's own ARKit session would draw it.

`CaptureResult.swift` decodes `outcome.arkitEpoch` and `recommendedPlacement.boxArkitWorld`:
pose is box-centre-to-world, 16 numbers column-major, local +x along the wall, +y up, +z out
of the wall, size width/height/depth in ARKit metres. These tests take the committed
mini-arkit packet's camera poses (camera-to-world in the phone's `.gravity` ARKit world,
epoch `e1`), build a scene that claims that world, place a known box in scene metres, and
check `ar.box_to_arkit` against the same box rebuilt through an independent scene-to-ARKit
fit taken from the packet's poses alone -- `frame.from_source` is never touched by the
independent path.

Tolerances. The wire pose is rounded to 1e-6 by `mat4_to_list`, so comparing two wire
poses allows 2.5e-6 (about 3 um, three orders of magnitude tighter than the millimetre
precision a phone render needs). Rotations are compared entry-wise with 1e-5 (the same
rounding, ~0.0006 deg). Fit-to-fit transform agreement is exact-synthetic data, so 1e-9.
The packet's four keyframe centres are near-collinear (collinearity 0.076), so the
independent fit uses the rotation-first chordal method (`align_by_rotations`), which is
well posed for collinear centres, rather than a bare Umeyama on centres.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from server.result import ar
from server.result.harness import chain as chain_mod
from server.result.harness import frame as frame_mod
from server.result.harness import sceneio
from server.result.tests.support import scene_dict

PACKET = Path(__file__).resolve().parents[3] / "data" / "fixtures" / "packets" / "mini-arkit" / "packet.json"
PHOTO_SCENE = Path(__file__).resolve().parents[3] / "server" / "result" / "tests" / "data" / "photo" / "scene.json"

BOX_CENTER = (1.2, 0.5, 0.17)  # depth/2 + STANDOFF_M out of wall w1, like compose's _placement
BOX_SIZE = (1.2, 1.0, 0.3)


def packet_keyframes() -> list[dict]:
    packet = json.loads(PACKET.read_text())
    assert [e["id"] for e in packet["epochs"]] == ["e1"]
    assert packet["session"]["worldAlignment"] == "gravity"
    return packet["keyframes"]


def similarity(yaw_deg: float, scale: float, tilt: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """Ground-truth arkit->scene similarity: tilt about x then z, yaw about y, uniform scale."""
    rx, rz = (math.radians(a) for a in tilt)

    def rot(axis: int, angle: float) -> np.ndarray:
        c, s = math.cos(angle), math.sin(angle)
        if axis == 0:
            return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], float)
        if axis == 2:
            return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], float)
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], float)

    pose = np.eye(4)
    pose[:3, :3] = scale * rot(2, rz) @ rot(0, rx) @ rot(1, math.radians(yaw_deg))
    pose[:3, 3] = (0.3, -0.1, 0.4)
    return pose


def scene_from_packet(source_frame: str, s: np.ndarray) -> dict:
    """A contract-valid scene built in world `s`: keyframes and frame claim both come from `s`."""
    scene = scene_dict(source_frame=source_frame, with_keyframe=False)
    scene["frame"]["from_source"] = [float(x) for x in s.flatten(order="F")]
    scene["keyframes"] = [
        {
            "id": kf["id"],
            "image": f"{kf['id']}.jpg",
            "pose": [
                float(x) for x in frame_mod.transform_pose(s, frame_mod.mat4_from_list(kf["pose"])).flatten(order="F")
            ],
            "intrinsics": list(kf["intrinsics"]),
            "w": kf["w"],
            "h": kf["h"],
            "pose_source": "ground_truth",
        }
        for kf in packet_keyframes()
    ]
    sceneio.validate(scene)
    return scene


def independent_scene_to_arkit(scene: dict) -> np.ndarray:
    """Scene->ARKit from the keyframe correspondences alone; never reads `frame.from_source`."""
    ark = [frame_mod.mat4_from_list(kf["pose"]) for kf in packet_keyframes()]
    sce = [frame_mod.mat4_from_list(kf["pose"]) for kf in scene["keyframes"]]
    return frame_mod.align_by_rotations(
        np.asarray([T[:3, :3] for T in sce], float),
        np.asarray([T[:3, 3] for T in sce], float),
        np.asarray([T[:3, :3] for T in ark], float),
        np.asarray([T[:3, 3] for T in ark], float),
    )


def box_pose_arkit(t_sa: np.ndarray, yaw_deg: float, center=BOX_CENTER) -> np.ndarray:
    """The box pose in ARKit, built from the app's contract, not from `ar.box_to_arkit`."""
    pose = np.eye(4)
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    pose[:3, :3] = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])  # +x along wall, +y up, +z out
    pose[:3, 3] = center
    return frame_mod.transform_pose(t_sa, pose)


def assert_box_matches(built: dict, expected: np.ndarray, expected_size: list[float]) -> None:
    pose = frame_mod.mat4_from_list(built["pose"])
    assert np.linalg.norm(pose[:3, 3] - expected[:3, 3]) < 2.5e-6
    assert np.linalg.norm(pose[:3, :3] - expected[:3, :3]) < 1e-5
    assert len(built["pose"]) == 16 and len(built["size"]) == 3
    assert np.allclose(np.linalg.det(pose[:3, :3]), 1.0, atol=1e-5)
    assert np.abs(np.asarray(built["size"], float) - np.asarray(expected_size, float)).max() < 1e-9


def test_box_round_trips_through_the_packet_s_arkit_poses():
    s = similarity(yaw_deg=8.0, scale=1.05)
    scene = scene_from_packet("arkit_world/e1", s)

    t_sa = ar.scene_to_arkit(scene["frame"])
    assert t_sa is not None
    assert ar.epoch_id(scene["frame"], epochs=["e1"]) == "e1"

    t_indep = independent_scene_to_arkit(scene)
    assert np.linalg.norm(t_indep - t_sa) < 1e-9

    yaw = ar.yaw_deg(chain_mod.Chain(scene).walls[0].along)
    assert abs(yaw) < 1e-9  # wall w1 runs along scene +x

    built = ar.box_to_arkit(t_sa, BOX_CENTER, BOX_SIZE, yaw)
    expected = box_pose_arkit(t_indep, yaw)
    assert_box_matches(built, expected, [v / 1.05 for v in BOX_SIZE])

    # Gravity-aligned scene: the box's up axis is vertical in the phone's .gravity world.
    up = frame_mod.mat4_from_list(built["pose"])[:3, :3] @ np.array([0.0, 1.0, 0.0])
    assert np.linalg.norm(up - np.array([0.0, 1.0, 0.0])) < 1e-5

    # A rotated box carries its yaw: same fit, standard rotation matrix, 25 degrees.
    built25 = ar.box_to_arkit(t_sa, BOX_CENTER, BOX_SIZE, 25.0)
    assert_box_matches(built25, box_pose_arkit(t_indep, 25.0), [v / 1.05 for v in BOX_SIZE])


def test_scene_without_arkit_epoch_omits_the_box():
    # The packet carries ARKit poses, but this scene was not built in one: no box may be
    # invented from the packet's poses, because the packet records the phone's raw walk
    # while only `frame.from_source` measures how scene metres relate to a source world.
    scene = scene_from_packet("synthetic", s=similarity(yaw_deg=8.0, scale=1.05))
    assert ar.scene_to_arkit(scene["frame"]) is None
    assert ar.epoch_id(scene["frame"], ["e1"]) is None

    # The photo-processing scene is the shipped case: photos in, no ARKit world claimed.
    photo = json.loads(PHOTO_SCENE.read_text())
    assert ar.scene_to_arkit(photo["frame"]) is None
    assert ar.epoch_id(photo["frame"], ["e1"]) is None


def test_gravity_misaligned_scene_carries_the_tilt():
    # A producer may hand back a scene whose up axis is not vertical in the ARKit world.
    # The box must lean with the reconstructed geometry -- where the phone would draw the
    # scene -- and must not be silently snapped upright.
    s = similarity(yaw_deg=8.0, scale=1.05, tilt=(3.0, -2.0))
    scene = scene_from_packet("arkit_world/e1", s)

    t_sa = ar.scene_to_arkit(scene["frame"])
    t_indep = independent_scene_to_arkit(scene)
    assert np.linalg.norm(t_indep - t_sa) < 1e-9

    built = ar.box_to_arkit(t_sa, BOX_CENTER, BOX_SIZE, 25.0)
    assert_box_matches(built, box_pose_arkit(t_indep, 25.0), [v / 1.05 for v in BOX_SIZE])

    rotation = frame_mod.mat4_from_list(built["pose"])[:3, :3]
    up = rotation @ np.array([0.0, 1.0, 0.0])
    out = rotation @ np.array([0.0, 0.0, 1.0])
    tilt_up = math.degrees(math.acos(float(np.clip(up @ np.array([0.0, 1.0, 0.0]), -1.0, 1.0))))
    tilt_out = math.degrees(math.acos(float(np.clip(out @ np.array([0.0, 0.0, 1.0]), -1.0, 1.0))))
    assert tilt_up > 0.5 and tilt_out > 0.5  # the tilt survived, not snapped away
    expected_up = np.linalg.inv(s)[:3, :3] @ np.array([0.0, 1.0, 0.0])
    angle = math.degrees(
        math.acos(float(np.clip(expected_up @ up / (np.linalg.norm(expected_up) * np.linalg.norm(up)), -1.0, 1.0)))
    )
    assert angle < 1e-4


def test_epoch_mismatch_between_packet_segments():
    # Two segments in one capture: the scene names its world, and that name wins.
    scene = scene_from_packet("arkit_world/e1", s=similarity(yaw_deg=8.0, scale=1.05))
    assert ar.epoch_id(scene["frame"], ["e1", "e2"]) == "e1"

    # A bare `arkit_world` over a two-segment capture names nothing: the app withholds AR
    # rather than drawing the box in a world nobody can confirm.
    bare = scene_from_packet("arkit_world", s=similarity(yaw_deg=8.0, scale=1.05))
    assert ar.epoch_id(bare["frame"], ["e1", "e2"]) is None
    assert ar.epoch_id(bare["frame"], ["e1"]) == "e1"  # one segment: unambiguous

    # An epoch the packet never saw is reported verbatim; catching it is the app's check
    # against its own recording, not the composer's.
    foreign = scene_from_packet("arkit_world/e9", s=similarity(yaw_deg=8.0, scale=1.05))
    assert ar.epoch_id(foreign["frame"], ["e1"]) == "e9"
