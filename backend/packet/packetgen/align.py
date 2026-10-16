"""Clean reconstruction to ARKit's world: gravity (y up), metric scale, origin and heading.

Gravity: SfM worlds have no "up". The mean camera up vector is biased by how the photographer
pitched the camera (12 degrees on ETH3D facade), so up is solved from scene planes: vertical
planes (walls) must contain it and horizontal planes (ground) must be normal to it.

Scale: ETH3D ground truth is metric. For SfM output the scale comes from the camera height above
the fitted ground plane, assumed 1.40 m (a phone held at chest to eye height), or, with no ground
plane, from an assumed 6 m median viewing distance. The truth sidecar records which, with its
relative uncertainty, so no score treats a pseudo-metric house as laser-grade.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .sources import Recon

ASSUMED_CAMERA_HEIGHT_M = 1.40
ASSUMED_CAMERA_HEIGHT_REL_SD = 0.12
ASSUMED_VIEW_DISTANCE_M = 6.0


@dataclass
class Plane:
    normal: np.ndarray
    offset: float  # n . x = offset
    inliers: np.ndarray  # indices into the point array used for fitting


@dataclass
class Alignment:
    up_src: np.ndarray
    up_from_cameras_src: np.ndarray
    up_vs_cameras_deg: float
    up_vs_hint_deg: float | None
    scale: float
    scale_source: str
    scale_rel_sd: float
    camera_height_m: float | None
    origin_src: np.ndarray
    R_align: np.ndarray  # rows: ARKit world axes in source coordinates
    planes: list[dict] = field(default_factory=list)

    def point(self, X_src: np.ndarray) -> np.ndarray:
        return self.scale * (np.asarray(X_src) - self.origin_src) @ self.R_align.T

    def pose(self, T_cv_src: np.ndarray) -> np.ndarray:
        """OpenCV camera-to-world (source) to ARKit camera-to-world (ARKit world)."""
        T = np.eye(4)
        T[:3, :3] = self.R_align @ T_cv_src[:3, :3] @ np.diag([1.0, -1.0, -1.0])
        T[:3, 3] = self.point(T_cv_src[:3, 3])
        return T


def ransac_planes(P: np.ndarray, threshold: float, max_planes: int = 8, min_frac: float = 0.03,
                  iters: int = 400, rng: np.random.Generator | None = None) -> list[Plane]:
    rng = rng or np.random.default_rng(0)
    remaining = np.arange(len(P))
    planes = []
    min_inliers = max(50, int(min_frac * len(P)))
    for _ in range(max_planes):
        if len(remaining) < min_inliers:
            break
        Q = P[remaining]
        best, best_n, best_d = None, None, None
        for _ in range(iters):
            a, b, c = Q[rng.choice(len(Q), 3, replace=False)]
            n = np.cross(b - a, c - a)
            nn = np.linalg.norm(n)
            if nn < 1e-12:
                continue
            n = n / nn
            d = n @ a
            inl = np.flatnonzero(np.abs(Q @ n - d) < threshold)
            if best is None or len(inl) > len(best):
                best, best_n, best_d = inl, n, d
        if best is None or len(best) < min_inliers:
            break
        X = Q[best]
        mu = X.mean(0)
        _, _, Vt = np.linalg.svd(X - mu, full_matrices=False)
        n = Vt[2]
        d = n @ mu
        inl = np.flatnonzero(np.abs(Q @ n - d) < threshold)
        planes.append(Plane(n, float(d), remaining[inl]))
        remaining = np.delete(remaining, inl)
    return planes


def solve_up(planes: list[Plane], u0: np.ndarray, weights: np.ndarray, tol_deg: float = 30.0) -> tuple[np.ndarray, list[str]]:
    """Up minimising sum_w (n.u)^2 over vertical planes and |n x u|^2 over horizontal ones.
    A plane votes only when it is within tol_deg of vertical or horizontal for the current u."""
    kinds = []
    M = np.zeros((3, 3))
    for p, w in zip(planes, weights):
        ang = np.degrees(np.arccos(np.clip(abs(p.normal @ u0), 0, 1)))
        if ang < tol_deg:
            M += w * (np.eye(3) - np.outer(p.normal, p.normal))
            kinds.append("horizontal")
        elif ang > 90 - tol_deg:
            M += w * np.outer(p.normal, p.normal)
            kinds.append("vertical")
        else:
            kinds.append("oblique")
    if not any(k != "oblique" for k in kinds):
        return u0, kinds
    vals, vecs = np.linalg.eigh(M)
    u = vecs[:, 0]
    return (u if u @ u0 > 0 else -u), kinds


def angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.degrees(np.arccos(np.clip(a @ b / np.linalg.norm(a) / np.linalg.norm(b), -1, 1))))


def align(recon: Recon, rng: np.random.Generator | None = None) -> Alignment:
    rng = rng or np.random.default_rng(0)
    C = np.array([v.T_cv[:3, 3] for v in recon.views])
    ups = np.array([-v.T_cv[:3, 1] for v in recon.views])
    u0 = ups.mean(0)
    u0 /= np.linalg.norm(u0)

    P = recon.points
    # Keep points near the cameras: far background (trees, sky noise) makes poor planes.
    dmin = np.min(np.linalg.norm(P[:, None, :] - C[None, ::max(1, len(C) // 40), :], axis=2), axis=1)
    near = P[dmin < np.percentile(dmin, 90)]
    if len(near) > 60000:
        near = near[rng.choice(len(near), 60000, replace=False)]
    scene_scale = float(np.median(dmin))
    planes = ransac_planes(near, threshold=0.02 * scene_scale, rng=rng)
    w = np.array([len(p.inliers) for p in planes], float)
    up = u0
    for tol in (30.0, 12.0, 5.0, 5.0):
        up, kinds = solve_up(planes, up, w, tol)

    # Ground: horizontal planes below most cameras; take the one with most inliers.
    cam_heights, ground = None, None
    for p, k in zip(planes, kinds):
        if k != "horizontal":
            continue
        n = p.normal if p.normal @ up > 0 else -p.normal
        d = p.offset if p.normal @ up > 0 else -p.offset
        h = C @ n - d
        if np.mean(h > 0) > 0.8 and (ground is None or len(p.inliers) > len(ground[0].inliers)):
            ground, cam_heights = (p, n, d), h
    ground_source = "ground_plane"
    if cam_heights is None:
        # Rough or sloped ground makes no plane; the lowest nearby points still mark it.
        low = np.percentile(near @ up, 2)
        h = C @ up - low
        if np.mean(h > 0) > 0.8:
            cam_heights, ground_source = h, "low_points"
    if recon.metric:
        scale, source, rel_sd = 1.0, "metric_reference", 0.0
    elif cam_heights is not None:
        scale = ASSUMED_CAMERA_HEIGHT_M / float(np.median(cam_heights))
        source = f"assumed_camera_height_1.40m_{ground_source}"
        rel_sd = ASSUMED_CAMERA_HEIGHT_REL_SD if ground_source == "ground_plane" else 0.2
    else:
        view_d = []
        for v in recon.views:
            if len(v.point_ids) == 0:
                continue
            idx = np.searchsorted(recon.point_ids, v.point_ids)
            view_d.append(np.median(np.linalg.norm(P[idx] - v.T_cv[:3, 3], axis=1)))
        scale = ASSUMED_VIEW_DISTANCE_M / float(np.median(view_d))
        source, rel_sd = "assumed_view_distance_6m", 0.4

    f = recon.views[0].T_cv[:3, 2]  # OpenCV +z: viewing direction of the first frame
    fh = f - (f @ up) * up
    if np.linalg.norm(fh) < 1e-6:
        fh = np.cross(up, [1.0, 0, 0])
    z_w = -fh / np.linalg.norm(fh)
    x_w = np.cross(up, z_w)
    R_align = np.stack([x_w, up, z_w])

    hint = None if recon.up_hint is None else angle_deg(up, recon.up_hint)
    return Alignment(
        up_src=up,
        up_from_cameras_src=u0,
        up_vs_cameras_deg=angle_deg(up, u0),
        up_vs_hint_deg=hint,
        scale=float(scale),
        scale_source=source,
        scale_rel_sd=rel_sd,
        camera_height_m=None if cam_heights is None else float(np.median(cam_heights) * scale),
        origin_src=C[0].copy(),
        R_align=R_align,
        planes=[
            {"kind": k, "normal_src": p.normal.tolist(), "offset_src": p.offset, "inliers": int(len(p.inliers))}
            for p, k in zip(planes, kinds)
        ],
    )
