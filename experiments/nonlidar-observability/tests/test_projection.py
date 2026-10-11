"""Hand-projection checks for the finite camera model.

Every expected pixel here was computed by hand from the bindings in
projection.py's docstring, not read back out of the code under test:

- identity camera at the origin, looking along -z: a point 10 ft in front lands
  on the principal point; a point 6 ft right and 12 ft deep lands at
  u = cx + fx * (6/12);
- a camera at (10, 0, 0) looking along -x (rotation about +y by +90 degrees):
  a point 5 ft to the camera's left at depth 10 lands at u = cx - fx/2, and one
  3 ft up lands at v = cy - fy * (3/10);
- the column-major pose reading must agree with recon/recon/capture.py's
  column_major on the same input (that function is the worker's own binding);
- project then backproject returns a ray through the point;
- points behind the camera or outside the frame produce no observation.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from nonlidar_observability.projection import (
    Camera,
    make_camera,
    to_column_major_pose,
)

FX, W, H = 1000.0, 1920, 1440
CX, CY = W / 2, H / 2


def identity_camera() -> Camera:
    return Camera(
        keyframe_id="kf",
        center=np.zeros(3),
        rotation=np.eye(3),
        fx=FX,
        fy=FX,
        cx=CX,
        cy=CY,
        width=W,
        height=H,
    )


def test_hand_check_identity_camera() -> None:
    cam = identity_camera()
    seen = cam.project(np.array([[0.0, 0.0, -10.0], [6.0, 0.0, -12.0], [0.0, 4.0, -8.0]]))
    assert seen[(0, 0)] == (CX, CY)
    assert seen[(1, 0)] == (CX + FX * 0.5, CY)
    assert seen[(2, 0)] == (CX, CY - FX * 0.5)  # above the axis: v runs down


def test_hand_check_yawed_camera() -> None:
    # Camera at (10, 0, 0) looking along -x. Rotation about +y by +90 degrees:
    # camera +x maps to world -z, camera +z maps to world +x.
    rotation = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]])
    cam = Camera("kf", np.array([10.0, 0.0, 0.0]), rotation, FX, FX, CX, CY, W, H)
    # A point 5 ft to the camera's left (world +z here), 10 ft deep.
    seen = cam.project(np.array([[0.0, 0.0, 5.0], [0.0, 3.0, 5.0]]))
    assert seen[(0, 0)] == (CX - FX * 0.5, CY)
    assert seen[(1, 0)] == (CX - FX * 0.5, CY - FX * 0.3)


def test_pose_binding_matches_recon_column_major() -> None:
    """The model's pose reading is the worker's pose reading, on the same bytes."""
    spec = importlib.util.spec_from_file_location(
        "recon_capture",
        Path(__file__).resolve().parents[3] / "recon" / "recon" / "capture.py",
    )
    assert spec is not None and spec.loader is not None
    recon_capture = importlib.util.module_from_spec(spec)
    sys.modules["recon_capture"] = recon_capture
    spec.loader.exec_module(recon_capture)

    cam = make_camera("kf", (3.0, -4.0, 12.0), (-5.0, 2.0, -20.0), fx=FX, width=W, height=H)
    pose = to_column_major_pose(cam.rotation, cam.center)
    theirs = recon_capture.column_major(pose)
    np.testing.assert_allclose(theirs[:3, 3], cam.center, atol=1e-12)
    np.testing.assert_allclose(theirs[:3, :3], cam.rotation, atol=1e-12)

    # And the schema-shaped keyframe dict reads back to the same camera.
    keyframe = {
        "id": "kf",
        "pose": pose,
        "intrinsics": [FX, FX, CX, CY],
        "w": W,
        "h": H,
        "img": "kf.jpg",
    }
    bound = Camera.from_keyframe(keyframe)
    np.testing.assert_allclose(bound.center, cam.center, atol=1e-12)
    np.testing.assert_allclose(bound.rotation, cam.rotation, atol=1e-12)
    assert (bound.width, bound.height) == (W, H)


def test_round_trip_through_backproject() -> None:
    cam = make_camera("kf", (3.0, 5.0, -6.0), (0.0, 4.0, -20.0), fx=FX, width=W, height=H)
    point = np.array([-7.0, 8.0, -20.0])
    (u, v) = next(iter(cam.project(point[None, :]).values()))
    origin, direction = cam.backproject(u, v)
    to_point = point - origin
    np.testing.assert_allclose(to_point / np.linalg.norm(to_point), direction, atol=1e-12)


def test_points_the_camera_cannot_see() -> None:
    cam = identity_camera()
    # Behind the camera, and outside the frame to the right.
    seen = cam.project(np.array([[0.0, 0.0, 10.0], [5000.0, 0.0, -10.0]]))
    assert seen == {}


def test_from_keyframe_rejects_short_pose() -> None:
    with pytest.raises(ValueError, match="16"):
        Camera.from_keyframe(
            {"id": "kf", "pose": [0.0] * 15, "intrinsics": [FX] * 4, "w": W, "h": H}
        )
