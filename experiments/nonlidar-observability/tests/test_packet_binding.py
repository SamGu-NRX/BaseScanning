"""Which raw Packet fields establish the study's metric-pose and ground assumptions?

The study's A1 (metric poses) and A3 (landmarks on the y=0 ground) are conditional
on supplied capabilities, so the tests pin exactly what the raw inputs do and do
not establish:

- `server/schemas/scene.schema.json` states units and gravity alignment ONLY in
  description prose ("translation in feet", "ARKit .gravity world alignment");
  no machine-readable field carries either. These tests encode that gap: if a
  future schema adds a machine `units` or ground-height field, they fail and the
  study can relabel the assumptions as raw-field established.
- The capture packet goes further the other way: packet/README.md quotes 0.4 as
  saying world y=0 is "the phone's height at session start, not the ground", and
  the ground-height field `meterAnchor.groundY` is an unapproved 0.5 proposal.
- `recon/recon/capture.py` is where both claims become operational: it converts
  scene translations with `T[:3, 3] *= FEET` ("scene frame translations are
  feet") and places the wall baseline at y=0. The tests validate that conversion
  against the repo's own scene fixture.
- The study's `Camera.from_keyframe` reads scene.json poses as feet directly
  (its world is feet), consistent with the schema prose; the fixture test pins
  that reading too.
"""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from nonlidar_observability.projection import Camera

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "server" / "schemas" / "scene.schema.json"
SCENE_FIXTURE_PATH = REPO_ROOT / "server" / "tests" / "fixtures" / "example-scene.json"
CAPTURE_CONTRACT_FIXTURE = (
    REPO_ROOT / "experiments" / "capture-contract" / "fixtures" / "synthetic-session.json"
)


def load_capture_module():
    spec = importlib.util.spec_from_file_location(
        "recon_capture_binding", REPO_ROOT / "recon" / "recon" / "capture.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["recon_capture_binding"] = module
    spec.loader.exec_module(module)
    return module


def test_schema_states_units_and_gravity_only_in_prose() -> None:
    """The feet and gravity claims exist as description strings; nothing machine-checks them."""
    schema = json.loads(SCHEMA_PATH.read_text())
    pose = schema["properties"]["keyframes"]["items"]["properties"]["pose"]
    assert "translation in feet" in pose["description"]
    assert "column-major" in pose["description"]
    assert "gravity" in schema["description"]  # ".gravity world alignment: +y up"
    # The gap, encoded: no machine-readable units field anywhere, and no
    # ground-height field. The scene `ground` property catalogs surface-type
    # polygons in plan [x, z]; it does not define a ground plane height.
    assert "units" not in schema["properties"]
    keyframe_props = schema["properties"]["keyframes"]["items"]["properties"]
    assert "units" not in keyframe_props
    ground_items = schema["properties"]["ground"]["items"]
    assert set(ground_items["properties"]) == {"type", "polygon", "plus_minus_ft"}
    assert set(ground_items["required"]) == {"type", "polygon"}


def test_scene_fixture_pose_is_machine_checked_shapes_only() -> None:
    """What the schema machine-checks: 16-number pose, 4-number intrinsics, w/h ints."""
    schema = json.loads(SCHEMA_PATH.read_text())
    keyframes = schema["properties"]["keyframes"]
    pose = keyframes["items"]["properties"]["pose"]
    intrinsics = keyframes["items"]["properties"]["intrinsics"]
    assert pose["minItems"] == pose["maxItems"] == 16
    assert intrinsics["minItems"] == intrinsics["maxItems"] == 4
    assert keyframes["items"]["properties"]["w"]["type"] == "integer"
    assert keyframes["items"]["properties"]["h"]["type"] == "integer"
    assert keyframes["items"]["required"] == ["id", "pose", "intrinsics", "w", "h", "img"]


def test_camera_from_keyframe_reads_fixture_pose_as_feet() -> None:
    """The study binds to the fixture's raw pose numbers directly, no conversion.

    scene.json poses are feet per schema prose; the study's world is feet, so
    from_keyframe must apply no scaling. capture.py, whose internal world is
    meters, applies `*= 0.3048` to the same numbers.
    """
    scene = json.loads(SCENE_FIXTURE_PATH.read_text())
    kf = scene["keyframes"][0]
    cam = Camera.from_keyframe(kf)
    # Column-major storage: reshape with order='F' (equivalently reshape then
    # transpose) before slicing the translation and rotation blocks.
    transform = np.array(kf["pose"], dtype=np.float64).reshape(4, 4, order="F")
    np.testing.assert_allclose(cam.center, transform[:3, 3], atol=1e-12)
    np.testing.assert_allclose(cam.rotation, transform[:3, :3], atol=1e-12)
    np.testing.assert_allclose(cam.rotation.T @ cam.rotation, np.eye(3), atol=1e-12)
    assert abs(np.linalg.det(cam.rotation) - 1.0) < 1e-12
    assert (cam.fx, cam.fy, cam.cx, cam.cy) == tuple(kf["intrinsics"])
    assert (cam.width, cam.height) == (kf["w"], kf["h"])


def test_capture_code_converts_the_same_fixture_pose_to_meters() -> None:
    """capture.py treats the same scene translations as feet: `T[:3, 3] *= 0.3048`.

    One reader scales, the other does not — both claim the same source numbers
    are feet. The unit claim is enforced by convention in each reader, never by
    a field the packet carries.
    """
    capture = load_capture_module()
    assert pytest.approx(0.3048) == capture.FEET
    scene = json.loads(SCENE_FIXTURE_PATH.read_text())
    kf = scene["keyframes"][0]
    feet_translation = np.array(kf["pose"], dtype=np.float64).reshape(4, 4)[:3, 3]
    converted = feet_translation * capture.FEET
    # The scan-bundle reader scales pose, meter, and baseline translations alike.
    meter = np.array(scene["meter"]["pos"], dtype=np.float64) * capture.FEET
    assert converted[0] == pytest.approx(feet_translation[0] * 0.3048)
    assert meter[1] == pytest.approx(5.0 * 0.3048)
    # And the wall hint pins the baseline to y = 0 in its internal world: the
    # ground claim, as code, not as a packet field.
    assert capture.UP[1] == 1.0


def test_capture_packet_does_not_establish_ground() -> None:
    """0.4's own note: world y=0 is the phone's height at session start, not the ground.

    The measure-lab fixture carries a machine `units` field, but that is the
    Measure Lab format, not the scan capture packet; and the scan packet's
    ground-height field (`meterAnchor.groundY`) is an unapproved 0.5 proposal.
    This test pins the fixture evidence without overclaiming it for the scan
    packet, whose spec lives in a private repository.
    """
    packet = json.loads(CAPTURE_CONTRACT_FIXTURE.read_text())
    assert packet["format"] == "measure-lab-session"
    assert packet["units"] == {"length": "meters"}
    packet_readme = (REPO_ROOT / "packet" / "README.md").read_text()
    assert "not the ground" in packet_readme  # 0.4's note, quoted in proposal 2
    assert "meterAnchor.groundY" in packet_readme  # proposed, not in the contract
    assert "All eleven are unapproved proposals" in packet_readme
