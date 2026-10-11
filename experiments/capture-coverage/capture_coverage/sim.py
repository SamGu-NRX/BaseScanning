"""Simulated captures: plan a walk, render LiDAR depth from true geometry, write scan bundles.

The bundle is byte-compatible with what `recon.capture.load` reads: scene.json per the app
schema, per-frame JPEG photos, float32 depth maps with uint8 ARKit confidence. Poses and
intrinsics are what a phone would report - the truth stays in this package's Scene, which the
worker never sees. Depth simulates iPhone LiDAR: 8 mm Gaussian noise, dropout at depth edges
and at random pixels, no returns beyond 5 m, medium/high confidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .scenes import Scene

FEET = 0.3048
UP = np.array([0.0, 1.0, 0.0])
FRAME_W, FRAME_H = 640, 480
DEPTH_W, DEPTH_H = 256, 192
FOCAL = 460.0  # ~69 degree horizontal fov
LIDAR_MAX = 5.0  # metres; beyond this the scanner returns nothing
DEPTH_SIGMA = 0.008  # metres
EDGE_DROP_M = 0.10  # depth steps larger than this drop their surrounding pixels


@dataclass
class Walk:
    """Per-frame TRUE camera poses (what the photons saw). Reported poses are written separately."""

    positions: np.ndarray  # (N, 3) camera centres
    rotations: np.ndarray  # (N, 3, 3) cam_to_world rotation, columns = cam x, y, z axes
    reported_positions: np.ndarray  # (N, 3) what ARKit would say
    reported_rotations: np.ndarray  # (N, 3, 3)


def look_rotation(pos: np.ndarray, look_at: np.ndarray) -> np.ndarray:
    """ARKit camera axes for a camera at `pos` looking at `look_at`: +x right, +y up, -z forward."""
    fwd = look_at - pos
    fwd = fwd / np.linalg.norm(fwd)
    z_cam = -fwd
    x_cam = np.cross(fwd, UP)
    x_cam = x_cam / np.linalg.norm(x_cam)
    y_cam = np.cross(z_cam, x_cam)
    return np.stack([x_cam, y_cam, z_cam], axis=1)


def rot_axis_angle(axis: np.ndarray, ang: float) -> np.ndarray:
    axis = axis / np.linalg.norm(axis)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + np.sin(ang) * K + (1 - np.cos(ang)) * (K @ K)


def plan_walk(
    scene: Scene,
    x_from: float,
    x_to: float,
    step: float = 0.55,
    standoff: float = 2.4,
    pitch_look_y: float = 1.1,
    lookahead: float = 0.3,
    skew_deg: float = 0.0,
    drift: dict | None = None,
    seed: int = 0,
) -> Walk:
    """Walk parallel to the wall at `standoff`, looking slightly ahead and down.

    `skew_deg` tilts the walk's heading away from the wall line (the path drifts outward
    linearly) with exact poses - the reconstruction is still true, only the 65 degree gate and
    the ground strip change. `drift` ({"cross_track": m per m walked, "yaw_deg": ...}) simulates
    AR position error: the reported poses accumulate a bias while the photons stay on the true
    path. This is the honest camera-skew probe: claims are self-consistent with the reported
    poses and wrong in true coordinates.
    """
    rng = np.random.default_rng(seed)
    xs = np.arange(x_from, x_to + 1e-9, step)
    skew = np.tan(np.deg2rad(skew_deg))
    mid = (x_from + x_to) / 2
    true_pos = np.stack([xs, np.full(len(xs), 1.5), standoff + skew * (xs - mid)], axis=1)
    looks = np.stack(
        [xs + lookahead * np.sign(x_to - x_from + 1e-9), np.full(len(xs), pitch_look_y), np.zeros(len(xs))],
        axis=1,
    )
    R_true = np.array([look_rotation(p, l) for p, l in zip(true_pos, looks, strict=True)])
    rep_pos, rep_rot = true_pos.copy(), R_true.copy()
    if drift:
        walked = np.arange(len(xs)) * step
        cross = drift.get("cross_track", 0.0) * walked
        yaw = np.deg2rad(drift.get("yaw_deg", 0.0))
        for i in range(len(xs)):
            rep_pos[i] = true_pos[i] + np.array([0.0, 0.0, cross[i]])
            if yaw:
                rep_rot[i] = rot_axis_angle(UP, yaw * (i / max(len(xs) - 1, 1))) @ R_true[i]
        if drift.get("jitter"):
            rep_pos += rng.normal(0, 0.005, rep_pos.shape)
            for i in range(len(xs)):
                axis = rng.normal(size=3)
                rep_rot[i] = rot_axis_angle(axis, np.deg2rad(0.2) * rng.uniform()) @ rep_rot[i]
    return Walk(true_pos, R_true, rep_pos, rep_rot)


# ---- depth rendering ---------------------------------------------------------------


def render_depth(
    scene: Scene, pos: np.ndarray, R: np.ndarray, rng: np.Generator, sigma: float = DEPTH_SIGMA
) -> np.ndarray:
    """(DEPTH_H, DEPTH_W) forward distances (camera-space -z), NaN where nothing returns."""
    tris = scene.all_tris()
    fx = fy = FOCAL * DEPTH_W / FRAME_W
    cx, cy = (FRAME_W / 2) * DEPTH_W / FRAME_W, (FRAME_H / 2) * DEPTH_H / FRAME_H
    zbuf = np.full((DEPTH_H, DEPTH_W), np.nan)
    Rt = R.T
    for tri in tris:
        p_cam = (tri - pos) @ Rt  # (3, 3) rows = vertices in camera axes
        z = -p_cam[:, 2]  # forward distance
        if (z <= 0.02).all():
            continue
        u = cx + fx * p_cam[:, 0] / z
        v = cy - fy * p_cam[:, 1] / z
        u0, u1 = int(max(0, np.floor(u.min()) - 1)), int(min(DEPTH_W - 1, np.ceil(u.max()) + 1))
        v0, v1 = int(max(0, np.floor(v.min()) - 1)), int(min(DEPTH_H - 1, np.ceil(v.max()) + 1))
        if u1 < u0 or v1 < v0:
            continue
        us, vs = np.meshgrid(np.arange(u0, u1 + 1) + 0.0, np.arange(v0, v1 + 1) + 0.0)
        d = np.stack(
            [
                (u[1] - u[0]) * (vs - v[0]) - (v[1] - v[0]) * (us - u[0]),
                (u[2] - u[1]) * (vs - v[1]) - (v[2] - v[1]) * (us - u[1]),
                (u[0] - u[2]) * (vs - v[2]) - (v[0] - v[2]) * (us - u[2]),
            ]
        )
        inside = (d >= -1e-9).all(axis=0) | (d <= 1e-9).all(axis=0)
        if not inside.any():
            continue
        area = d[0] + d[1] + d[2]
        area = np.where(np.abs(area) < 1e-12, 1e-12, area)
        # d[0] spans (p0,p1,p): the weight of vertex 2; likewise d[1] -> vertex 0, d[2] -> vertex 1
        w2, w0, w1 = d[0] / area, d[1] / area, d[2] / area
        # perspective-correct: interpolate 1/z, then invert
        with np.errstate(divide="ignore"):
            inv = w0 / z[0] + w1 / z[1] + w2 / z[2]
        zi = 1.0 / inv
        ys, xs_ = np.where(inside)
        flat = (v0 + ys) * DEPTH_W + (u0 + xs_)
        cur = zbuf[v0 + ys, u0 + xs_]
        new = zi[ys, xs_]
        better = np.isnan(cur) | (new < cur)
        zbuf.flat[flat[better]] = new[better]
    zbuf[~(zbuf > 0)] = np.nan
    zbuf[zbuf > LIDAR_MAX] = np.nan
    # sensor behaviour: noise, dropout at depth discontinuities, sparse random dropouts
    zbuf += rng.normal(0, sigma, zbuf.shape)
    finite = np.isfinite(zbuf)
    edges = np.zeros_like(finite)
    for axis in (0, 1):
        a, b = np.roll(finite, 1, axis=axis), np.roll(finite, -1, axis=axis)
        dn = np.where(finite & a, np.abs(zbuf - np.roll(zbuf, 1, axis=axis)), 0.0)
        dp = np.where(finite & b, np.abs(zbuf - np.roll(zbuf, -1, axis=axis)), 0.0)
        edges |= (dn > EDGE_DROP_M) | (dp > EDGE_DROP_M)
    zbuf[edges] = np.nan
    drop = rng.random(zbuf.shape) < 0.004
    zbuf[drop] = np.nan
    return zbuf.astype(np.float32)


def render_confidence(depth: np.ndarray) -> np.ndarray:
    """ARKit confidence: 2 where depth returned, 1 in the ring around dropouts, 0 at dropouts.
    The worker drops conf < 1, so the ring is where medium confidence lives."""
    nan = np.isnan(depth)
    conf = np.where(nan, 0, 2).astype(np.uint8)
    ring = np.zeros_like(nan)
    for shift in (1, 2):
        for axis in (0, 1):
            near = np.zeros_like(ring)
            dst = [slice(None)] * 2
            src = [slice(None)] * 2
            dst[axis] = slice(shift, None)
            src[axis] = slice(None, -shift)
            near[tuple(dst)] |= nan[tuple(src)]  # a nan `shift` rows/cols back
            dst[axis] = slice(None, -shift)
            src[axis] = slice(shift, None)
            near[tuple(dst)] |= nan[tuple(src)]  # or `shift` ahead
            ring |= near
    conf[(ring > 0) & ~nan] = 1
    return conf


def render_photo(scene: Scene, pos: np.ndarray, R: np.ndarray) -> np.ndarray:
    """A 640x480 grey photo with a vertical gradient - the worker uses photos only for mesh
    colouring, never for geometry or visibility, so the study's claims do not depend on them."""
    v = (FRAME_H / 2 - np.arange(FRAME_H)[:, None]) / FOCAL
    img = np.clip(128 + 60 * v, 0, 255).astype(np.uint8)
    img = np.broadcast_to(img, (FRAME_H, FRAME_W)).copy()
    _ = scene, pos, R
    return img


# ---- bundle writing ----------------------------------------------------------------


def _pose_list(T_rot: np.ndarray, t_m: np.ndarray) -> list[float]:
    """column-major 4x4 with translation in feet, as capture._pose_ft reads it."""
    P = np.eye(4)
    P[:3, :3] = T_rot
    P[:3, 3] = t_m / FEET
    return [round(float(x), 6) for x in P.T.reshape(-1)]


@dataclass
class CaptureSpec:
    """What the phone attests beyond its sensors: taps, baselines, object and ground labels."""

    baseline: tuple[float, float]  # phone-marked wall span along x, metres (true coords here)
    baseline_extra: tuple[float, float] | None = None  # unsupported-ends variant: a longer mark
    ends: tuple[str, str] = ("unexplored", "unexplored")  # left, right: "limit" | "unexplored"
    meter_x: float = 0.0
    objects: list[dict] = field(default_factory=list)
    grounds: list[dict] | None = None  # override the scene's ground patches for the attestation
    wall_height_ft: float = 8.5


def write_bundle(
    root: Path,
    scene: Scene,
    walk: Walk,
    spec: CaptureSpec,
    *,
    drop_frames: list[int] | None = None,
    depthless_frames: list[int] | None = None,
    omit_confidence: bool = False,
    depth_sigma: float = DEPTH_SIGMA,
    seed: int = 0,
) -> Path:
    """Write the bundle `recon` reads. Frames are f000, f001, ...; depth lies beside them."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "frames").mkdir(exist_ok=True)
    rng = np.random.default_rng(seed + 7)
    keep = [i for i in range(len(walk.positions)) if i not in (drop_frames or [])]
    keyframes = []
    for n, i in enumerate(keep):
        fid = f"f{n:03d}"
        pos, R = walk.positions[i], walk.rotations[i]
        rep_pos, rep_R = walk.reported_positions[i], walk.reported_rotations[i]
        img = render_photo(scene, pos, R)
        cv2.imwrite(str(root / "frames" / f"{fid}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        kf: dict = {
            "id": fid,
            "img": f"frames/{fid}.jpg",
            "w": FRAME_W,
            "h": FRAME_H,
            "intrinsics": [FOCAL, FOCAL, FRAME_W / 2, FRAME_H / 2],
            "pose": _pose_list(rep_R, rep_pos),
        }
        if i not in (depthless_frames or []):
            depth = render_depth(scene, pos, R, rng, sigma=depth_sigma)
            depth.tofile(root / "frames" / f"{fid}.f32")
            kf["depth"] = {"file": f"frames/{fid}.f32", "w": DEPTH_W, "h": DEPTH_H}
            if not omit_confidence:
                conf = render_confidence(depth)
                conf.tofile(root / "frames" / f"{fid}.conf")
                kf["depth"]["confidenceFile"] = f"frames/{fid}.conf"
        keyframes.append(kf)
    bl = spec.baseline_extra or spec.baseline
    doc: dict = {
        "schema_version": "1.0",
        "type": "scan",
        "meter": {
            "pos": [round(spec.meter_x / FEET, 4), 0.0, round(0.05 / FEET, 4)],
            "wall_id": "wall",
        },
        "walls": [
            {
                "id": "wall",
                "baseline": [
                    [round(bl[0] / FEET, 4), 0.0],
                    [round(bl[1] / FEET, 4), 0.0],
                ],
                "height_ft": spec.wall_height_ft,
            }
        ],
        "keyframes": keyframes,
        "coverage": {
            "ends": {"left": {"kind": spec.ends[0]}, "right": {"kind": spec.ends[1]}},
        },
    }
    if spec.objects:
        doc["objects"] = spec.objects
    grounds = spec.grounds
    if grounds is None:
        grounds = [
            {
                "type": g.kind,
                "polygon": [
                    [round(g.x0 / FEET, 4), round(g.z0 / FEET, 4)],
                    [round(g.x1 / FEET, 4), round(g.z0 / FEET, 4)],
                    [round(g.x1 / FEET, 4), round(g.z1 / FEET, 4)],
                    [round(g.x0 / FEET, 4), round(g.z1 / FEET, 4)],
                ],
            }
            for g in scene.grounds
        ]
    if grounds:
        doc["ground"] = grounds
    (root / "scene.json").write_text(json.dumps(doc, indent=1))
    return root / "scene.json"
