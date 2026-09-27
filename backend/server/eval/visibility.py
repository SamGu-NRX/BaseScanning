"""What a set of cameras truly saw, given ground-truth surfaces (a laser scan).

Each camera gets a z-buffer of the truth points (plus occluders the scanner missed, e.g. ETH3D's
occlusion splats) at reduced resolution, with holes closed by a min filter, as in PR #12's
`visible_scan_points`. A point counts as seen by a camera when it projects inside the image, in
front of the camera, and no deeper than the z-buffer there (within a tolerance): it lies on the
visible surface or in the free space in front of it. An optional incidence limit (PR #13 used 75
degrees) keeps grazing views of a surface from counting as seeing it.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from server.eval.frame import project_arkit


@dataclass
class Camera:
    id: str
    pose: np.ndarray  # ARKit camera-to-scene
    K: np.ndarray  # fx, fy, cx, cy at full resolution
    w: int
    h: int


class VisibilityOracle:
    def __init__(self, cams: list[Camera], blockers: np.ndarray, scale: float = 0.125, window: int = 5,
                 max_depth: float = 60.0):
        self.cams = cams
        self.scale = scale
        self.zbuf: list[np.ndarray] = []
        kernel = np.ones((window, window), np.uint8)
        for c in cams:
            zw, zh = max(1, round(c.w * scale)), max(1, round(c.h * scale))
            u, v, z = project_arkit(c.pose, c.K, blockers)
            ok = (z > 0.2) & (z < max_depth) & (u >= 0) & (u < c.w) & (v >= 0) & (v < c.h)
            iu = np.clip((u[ok] * scale).astype(np.int64), 0, zw - 1)
            iv = np.clip((v[ok] * scale).astype(np.int64), 0, zh - 1)
            zb = np.full((zh, zw), np.float32(1e6))
            np.minimum.at(zb, (iv, iu), z[ok].astype(np.float32))
            self.zbuf.append(cv2.erode(zb, kernel))

    def depth_at(self, i: int, u: np.ndarray, v: np.ndarray) -> np.ndarray:
        zb = self.zbuf[i]
        iu = np.clip((u * self.scale).astype(np.int64), 0, zb.shape[1] - 1)
        iv = np.clip((v * self.scale).astype(np.int64), 0, zb.shape[0] - 1)
        return zb[iv, iu]

    def seen_by(self, X: np.ndarray, normals: np.ndarray | None = None, max_incidence_deg: float = 75.0,
                rel_tol: float = 0.03, abs_tol: float = 0.05) -> np.ndarray:
        """Boolean [n_points, n_cams]: which cameras see each point."""
        X = np.asarray(X, float)
        out = np.zeros((len(X), len(self.cams)), bool)
        cmin = np.cos(np.radians(max_incidence_deg))
        for i, c in enumerate(self.cams):
            u, v, z = project_arkit(c.pose, c.K, X)
            ok = (z > 0.3) & (u >= 0) & (u < c.w) & (v >= 0) & (v < c.h)
            idx = np.flatnonzero(ok)
            d = self.depth_at(i, u[idx], v[idx])
            keep = z[idx] <= d * (1 + rel_tol) + abs_tol
            if normals is not None:
                to_cam = c.pose[:3, 3] - X[idx]
                cosang = np.einsum("ij,ij->i", to_cam, normals[idx]) / np.linalg.norm(to_cam, axis=1)
                keep &= cosang >= cmin
            out[idx[keep], i] = True
        return out

    def rays(self, stride: int = 4, max_depth: float = 40.0, max_incidence_deg: float = 75.0):
        """Per camera: rays through a pixel grid of its z-buffer to the truth surface. Yields
        (origin, ends, end_is_surface), where end_is_surface is False for grazing hits (incidence
        above `max_incidence_deg`, normals from the z-buffer's own gradients): the space in front
        still counts as seen, the surface does not."""
        cmin = np.cos(np.radians(max_incidence_deg))
        for i, c in enumerate(self.cams):
            zb = self.zbuf[i].astype(np.float64)
            H, W = zb.shape
            vv, uu = np.mgrid[0:H, 0:W]
            fx, fy, cx, cy = c.K
            u = (uu + 0.5) / self.scale
            v = (vv + 0.5) / self.scale
            P = np.stack([(u - cx) / fx * zb, -(v - cy) / fy * zb, -zb], -1)  # camera coords
            du = np.gradient(P, axis=1)
            dv = np.gradient(P, axis=0)
            n = np.cross(du, dv)
            n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-12)
            ray = P / np.maximum(np.linalg.norm(P, axis=-1, keepdims=True), 1e-12)
            cosinc = np.abs(np.einsum("hwk,hwk->hw", n, ray))
            sel = np.zeros_like(zb, bool)
            sel[::stride, ::stride] = True
            ok = sel & (zb < max_depth)
            ends = P[ok] @ c.pose[:3, :3].T + c.pose[:3, 3]
            yield c.pose[:3, 3], ends, cosinc[ok] >= cmin
