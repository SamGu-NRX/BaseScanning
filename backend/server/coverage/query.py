"""Depth-aware coverage query (blockers, then a per-frame depth test)."""

from __future__ import annotations

import numpy as np

from server.eval.coverage import CoverageQuery
from server.eval.loader import load as load_harness


class DepthAwareCoverageQuery(CoverageQuery):
    """A probe is unseen when blockers hide it, or when a depth map is closer than the probe.

    Frames with a depth map but no value at ``(u, v)`` do not see the probe. Frames
    with no depth map use the blocker test only. ``(u, v)`` are full-resolution
    pixels; a quarter-resolution depth map scales them into the map.
    """

    def __init__(
        self,
        cams: list,
        blockers: np.ndarray,
        depths: dict[str, np.ndarray] | None = None,
        relief_m: float = 0.10,
        relief_rel: float = 0.04,
        **kwargs,
    ):
        blockers = np.asarray(blockers, float)
        if blockers.size == 0:
            blockers = np.zeros((0, 3))
        super().__init__(cams, blockers, **kwargs)
        self.depths = depths or {}
        self.relief_m = relief_m
        self.relief_rel = relief_rel

    def depth_hides(self, cam_index: int, u: float, v: float, z: float) -> bool:
        cam = self.cams[cam_index]
        depth = self.depths.get(cam.id)
        if depth is None:
            return False
        height, width = depth.shape
        ui = int(round(float(u) * width / max(int(cam.w), 1)))
        vi = int(round(float(v) * height / max(int(cam.h), 1)))
        if ui < 0 or vi < 0 or ui >= width or vi >= height:
            return True
        sample = float(depth[vi, ui])
        if not np.isfinite(sample):
            return True
        return sample + max(self.relief_m, self.relief_rel * z) < z

    def _mask_depth(self, points: np.ndarray, seen: np.ndarray) -> np.ndarray:
        frame = load_harness()["frame"]
        pts = np.asarray(points, float)
        out = np.array(seen, copy=True)
        if not self.depths:
            return out
        for index, cam in enumerate(self.cams):
            if cam.id not in self.depths:
                continue
            u, v, z = frame.project_arkit(cam.pose, cam.K, pts)
            for point_index in range(len(pts)):
                if not out[point_index, index]:
                    continue
                if self.depth_hides(index, float(u[point_index]), float(v[point_index]), float(z[point_index])):
                    out[point_index, index] = False
        return out

    def incidences(self, X: np.ndarray, normals: np.ndarray | None):
        seen, incidence, footprint = super().incidences(X, normals)
        seen = self._mask_depth(X, seen)
        if incidence is not None:
            incidence = np.where(seen, incidence, np.inf)
        if footprint is not None:
            footprint = np.where(seen, footprint, np.inf)
        return seen, incidence, footprint

    def seen_by(self, points: np.ndarray) -> np.ndarray:
        pts = np.asarray(points, float)
        blockers = self.oracle.seen_by(pts, None, max_incidence_deg=89.0)
        return self._mask_depth(pts, blockers)


def query_status(scene: dict, wall_id: str, bands: list[dict]) -> str:
    """``unobserved`` when the wall has no wall band. Otherwise the band is present."""
    wall_bands = [b for b in bands if b.get("wall_id") == wall_id and b.get("band") == "wall"]
    if not wall_bands:
        return "unobserved"
    return "observed"
