"""Copy a model into metres about ``origin_m``. Does not mutate the inputs."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class CameraRecord:
    image: str
    keyframe_id: str | None
    pose: np.ndarray
    intrinsics: list[float]
    w: int
    h: int

    def copy(self) -> CameraRecord:
        return CameraRecord(
            self.image, self.keyframe_id, np.array(self.pose, float, copy=True), list(self.intrinsics), self.w, self.h
        )


def scale_about(points, cameras, scale: float, origin) -> tuple[np.ndarray, list[CameraRecord]]:
    origin_v = np.asarray(origin, float)
    scaled_points = origin_v + float(scale) * (np.asarray(points, float) - origin_v)
    scaled_cameras = []
    for camera in cameras:
        pose = np.array(camera.pose, float, copy=True)
        pose[:3, 3] = origin_v + float(scale) * (pose[:3, 3] - origin_v)
        scaled_cameras.append(
            CameraRecord(camera.image, camera.keyframe_id, pose, list(camera.intrinsics), int(camera.w), int(camera.h))
        )
    return scaled_points, scaled_cameras
