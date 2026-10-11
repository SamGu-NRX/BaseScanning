"""The finite camera projection model, bound to the capture packet's raw fields.

Every field this module reads is a raw export field, and each binding is cited:

- ``keyframes[].pose`` — server/schemas/scene.schema.json: "Camera-to-world
  transform in the scene frame, 16 numbers in column-major order, translation in
  feet." The column-major reading (transpose of the row-major reshape, rotation
  orthonormal to export rounding) is ``recon/recon/capture.py::column_major``.
- ``keyframes[].intrinsics`` — same schema: "[fx, fy, cx, cy] in pixels of the
  unrotated (landscape, as the sensor reads it) image", with ``w`` and ``h`` its
  size. The pixel convention, image (0, 0) at the top-left, is the one
  ``recon/recon/capture.py`` states for the same fields.
- Camera axes — ARKit's: +x right, +y up, looking along -z
  (``recon/recon/capture.py`` module docstring).
- Scene frame — meters in the worker; the schema stores scene geometry and pose
  translation in feet, so this study works in feet throughout (schema:
  walls[].baseline "[x, z] points", keyframes[].pose "translation in feet").

The model is finite on purpose: one pinhole camera per keyframe, a fixed landmark
set, and nothing else. It renders no depth, no mesh, no plane fit, no coverage —
those are LiDAR-side or derived facts the no-LiDAR export does not carry, and
modeling them here would add capability the raw contract lacks. Projection and
its inverse (a pixel plus a known camera center is a ray) are the whole model.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# A point closer than this to the camera plane is treated as at/behind it. The
# export rounds poses and geometry, so exact zeros are meaningless anyway.
MIN_DEPTH_FT = 1e-6


@dataclass(frozen=True)
class Camera:
    """One keyframe's pinhole camera, in scene-frame feet and sensor pixels."""

    keyframe_id: str
    center: np.ndarray  # (3,), scene frame, feet: pose translation
    rotation: np.ndarray  # (3, 3) cam-to-world: pose rotation
    fx: float
    fy: float
    cx: float
    cy: float
    width: int  # keyframes[].w, unrotated sensor image
    height: int  # keyframes[].h

    @classmethod
    def from_keyframe(cls, keyframe: dict) -> Camera:
        """Bind the raw scene.json keyframe fields (schema 0.4) to the model.

        Accepts the dict shape of ``keyframes[]``: ``pose`` (16 column-major
        numbers), ``intrinsics`` ([fx, fy, cx, cy]), ``w``, ``h``, and ``id``.
        """
        pose = np.asarray(keyframe["pose"], dtype=np.float64)
        if pose.shape != (16,):
            raise ValueError(f"pose must have 16 numbers, got shape {pose.shape}")
        transform = pose.reshape(4, 4).T  # column-major storage
        fx, fy, cx, cy = (float(v) for v in keyframe["intrinsics"])
        return cls(
            keyframe_id=str(keyframe["id"]),
            center=transform[:3, 3].copy(),
            rotation=transform[:3, :3].copy(),
            fx=fx,
            fy=fy,
            cx=cx,
            cy=cy,
            width=int(keyframe["w"]),
            height=int(keyframe["h"]),
        )

    def project(self, points_ft: np.ndarray) -> dict[tuple[int, int], tuple[float, float]]:
        """Project world points (N, 3), scene-frame feet, to unrotated pixels.

        Returns a map from (row, point) to (u, v) for points that lie strictly in
        front of the camera and inside the frame. A point the camera cannot see
        (behind it, or outside ``w``/``h``) is absent: the export carries no
        observation of it.

        Derivation, from the bindings in the module docstring: with the camera
        looking along -z, a point in front has camera-frame z < 0, so the depth
        along the optical axis is ``-z_cam``. The image u axis runs with camera
        +x (right), and the image v axis runs down while camera +y is up, hence
        the sign flip on v: u = fx * x_cam / depth + cx, v = -fy * y_cam / depth + cy.
        """
        pts = np.atleast_2d(np.asarray(points_ft, dtype=np.float64))
        cam = (pts - self.center) @ self.rotation  # R^T @ (p - C), row-wise
        depth = -cam[:, 2]
        front = depth > MIN_DEPTH_FT
        out: dict[tuple[int, int], tuple[float, float]] = {}
        for i in range(pts.shape[0]):
            if not front[i]:
                continue
            u = self.fx * cam[i, 0] / depth[i] + self.cx
            v = -self.fy * cam[i, 1] / depth[i] + self.cy
            if 0.0 <= u <= self.width and 0.0 <= v <= self.height:
                out[(i, 0)] = (float(u), float(v))
        return out

    def backproject(self, u: float, v: float) -> tuple[np.ndarray, np.ndarray]:
        """The world ray a pixel observes: (origin, unit direction), feet.

        The inverse of ``project`` for in-frame points. Because the camera
        center is a raw export field (pose translation), a pixel alone fixes a
        full line in the scene frame; what it does not fix is where along the
        line the wall point sits. That one-degree-of-freedom gap is what the
        identifiability study measures.
        """
        dir_cam = np.array([(u - self.cx) / self.fx, -(v - self.cy) / self.fy, -1.0])
        dir_world = self.rotation @ dir_cam
        return self.center.copy(), dir_world / np.linalg.norm(dir_world)


def make_camera(
    keyframe_id: str,
    position_ft: tuple[float, float, float],
    target_ft: tuple[float, float, float],
    fx: float,
    width: int,
    height: int,
) -> Camera:
    """A camera for the study's world grids, looking at ``target_ft``.

    Builds the cam-to-world rotation for ARKit axes (+x right, +y up, -z
    forward): world forward f = target - position; camera +z in world is -f;
    camera +y is the world-up vector orthogonalized against it; camera +x =
    y x z. Same axes ``recon/recon/capture.py`` reads.
    """
    position = np.asarray(position_ft, dtype=np.float64)
    forward = np.asarray(target_ft, dtype=np.float64) - position
    forward = forward / np.linalg.norm(forward)
    z_world = -forward
    up = np.array([0.0, 1.0, 0.0])
    y_world = up - up.dot(z_world) * z_world
    y_world /= np.linalg.norm(y_world)
    x_world = np.cross(y_world, z_world)
    rotation = np.column_stack([x_world, y_world, z_world])
    fy = fx
    return Camera(
        keyframe_id=keyframe_id,
        center=position,
        rotation=rotation,
        fx=fx,
        fy=fy,
        cx=width / 2.0,
        cy=height / 2.0,
        width=width,
        height=height,
    )


def to_column_major_pose(rotation: np.ndarray, center: np.ndarray) -> list[float]:
    """A cam-to-world transform as the schema stores it: 16 column-major numbers.

    Inverse of the reading in ``Camera.from_keyframe``; the tests use it to check
    the binding against ``recon/recon/capture.py::column_major`` on the same
    input.
    """
    transform = np.eye(4)
    transform[:3, :3] = rotation
    transform[:3, 3] = center
    return transform.T.reshape(16).tolist()
