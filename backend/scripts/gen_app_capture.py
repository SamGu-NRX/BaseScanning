#!/usr/bin/env python3
"""Deterministic app-shaped capture packet with known ground truth.

The morning report's biggest risk is that the answer the phone gets back does
not yet depend on the scan: the only real-image packet in the repository
(``ios-kit/fixtures/mini-arkit-lidar``) has six frames and no ground truth for
the meter, hazards or clearances, and the ETH3D fixture store is unreachable
(``backend/docs/STATUS.md``). This script renders the packet the app would have
uploaded from a scene whose true geometry is exact, so the real CPU pipeline can
be checked against ground truth and two different walls can be shown to produce
different answers.

The renderer is pure numpy/PIL: no Blender, no downloads, no model calls. The
output layout copies ``ios-kit/fixtures/mini-arkit-lidar/packet.json`` (packet
spec v0.4) and passes ``server.packet`` stage ``validate`` and the ios-kit
schema. Next to ``packet.json`` the script writes ``ground_truth.json``: the
wall plane, the meter centre, hazard boxes and the true clearance distances in
metres.

Scene spec (JSON)::

    {
      "name": "clear-wall",            # packet/seed identity
      "seed": 1401,
      "wall": {"xMin": -1.0, "xMax": 5.0, "height": 2.8, "distance": 2.0},
      "camera": {"height": 1.45},      # device height above the ground
      "meter": {"x": 2.0, "centreHeight": 1.2, "size": [0.45, 0.65, 0.18]},
      "gasMeter": null,                # or {"x": ..., "centreHeight": ..., "size": [...]}
      "windows": [{"x": 3.6, "sillHeight": 1.0, "size": [1.2, 1.4]}],
      "vents": [{"x": 0.4, "centreHeight": 2.45, "size": [0.3, 0.15]}]
    }

World frame (matches the packet's own ``conventions``): ARKit ``.gravity`` —
y up, origin at the device position when the session started, the camera facing
``-z`` toward the wall. ``meter.x`` and ``centreHeight`` are metres along the
wall from the session origin and above the ground; "left of the meter" as the
homeowner sees the wall is ``-x``.

Usage::

    uv run python scripts/gen_app_capture.py \
        --spec tests/fixtures/app_capture_synth/clear-wall.json --out /tmp/clear-wall

Same spec and seed in, same bytes out: ids are uuid5 of the spec name and seed,
gzip members carry ``mtime=0``, JPEG quality is fixed and every timestamp is a
constant. Camera poses are exact (no drift): the packet *is* the ground truth.

"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sys
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.common.geometry import column_major, from_column_major, pixel_ray_arkit  # noqa: E402
from server.packet.conventions import DEVICE_FROM_CAMERA, project_arkit, rotmat_to_quat_xyzw  # noqa: E402

KEYFRAME_W, KEYFRAME_H = 640, 480
DEPTH_W, DEPTH_H = 256, 192
FX = FY = 554.25  # 60 deg horizontal FOV at 640 px
CX, CY = KEYFRAME_W / 2.0, KEYFRAME_H / 2.0
INTRINSICS = [FX, FY, CX, CY]

SESSION_START_UPTIME = 4800.0
WALK_SECONDS = 10.0
WALK_SPEED_MPS = 1.0
WALK_START_X = -2.0
N_KEYFRAMES = 30  # exactly 3 Hz, so every keyframe lands on the 30 Hz arkitPoses grid
KEYFRAME_DT = 1.0 / 3.0
JPEG_QUALITY = 92
FEATURES_PER_FRAME = 160
FEATURES_STRIDE_PX = 24

SUN = np.array([-0.45, 0.7, 0.56])
SUN = SUN / np.linalg.norm(SUN)

NSYNTH_FIELDS = [
    "keyframes[].pose, intrinsics, timestamp, exposure, exif, lightEstimate (exact walk model)",
    "keyframes[].depth and confidence (rays cast against the true scene geometry)",
    "keyframes[].features (deterministic grid over the rendered texture)",
    "streams.motion, streams.heading, streams.location, streams.imuRaw (walk model + seed noise)",
    "streams.arkitPoses (trajectory sampled on the 30 Hz grid)",
    "planes, taps (ray cast against the meter box)",
]

UNITS = {
    "length": "meters",
    "angle": "degrees",
    "time": "seconds of device uptime, the clock of ARFrame.timestamp and CMDeviceMotion.timestamp",
    "image": "pixels of the saved JPEG",
    "rotationRate": "radians per second",
    "acceleration": "g (9.80665 m/s^2)",
    "magneticField": "microtesla",
}

CONVENTIONS = {
    "world": (
        "ARKit world frame with worldAlignment .gravity: right-handed, y up (away from gravity), "
        "origin at the device's position when the session (epoch) started, -z the camera's initial "
        "facing projected onto the horizontal"
    ),
    "camera": (
        "ARKit camera frame: +x right and +y up in the unrotated landscape sensor image, the camera looks along -z"
    ),
    "device": "Core Motion device frame: +x right and +y toward the top with the phone upright in portrait, +z out of the screen",
    "pose": "camera-to-world 4x4 matrix, 16 numbers column by column (simd_float4x4 layout), meters",
    "intrinsics": "[fx, fy, cx, cy] in pixels of the saved, unrotated landscape image",
    "pixel": "[u, v] continuous image coordinates: (0, 0) is the top-left corner of the top-left pixel, v grows down",
    "ray": "origin is the camera position; direction is a unit vector; camera-space direction ((u - cx)/fx, -(v - cy)/fy, -1)",
    "geodetic": "WGS84 degrees; altitude above mean sea level, ellipsoidalAltitude above the WGS84 ellipsoid, meters",
    "heading": "degrees clockwise from north, 0 to 360",
}

SESSION_GATES = {
    "trackingStableSeconds": 1.0,
    "minimumGroundLookDown": 30.0,
    "minimumContactSeparation": 2.0,
    "maximumAngleFromWallNormal": 60.0,
    "wallValidationTolerance": 0.0508,
    "minimumCameraOffsetFromWall": 0.1,
    "minimumRayAngle": 15.0,
    "maximumRayGap": 0.0508,
    "keyframeSpacingMeters": 0.5,
    "keyframeSpacingDegrees": 15.0,
}


# --------------------------------------------------------------------------------------
# Scene model
# --------------------------------------------------------------------------------------


class Scene:
    """The wall world, in exact metres. Every box is axis-aligned."""

    def __init__(self, spec: dict) -> None:
        self.spec = spec
        self.seed = int(spec["seed"])
        self.name = str(spec["name"])
        wall = spec["wall"]
        self.wall_x0 = float(wall["xMin"])
        self.wall_x1 = float(wall["xMax"])
        self.wall_height = float(wall["height"])
        self.wall_z = -float(wall["distance"])  # wall plane, cameras on the +z side
        self.camera_height = float(spec.get("camera", {}).get("height", 1.45))
        self.ground_y = -self.camera_height
        meter = spec["meter"]
        self.meter = {
            "kind": "electric",
            "x": float(meter["x"]),
            "centre_y": self.ground_y + float(meter["centreHeight"]),
            "size": [float(v) for v in meter["size"]],
        }
        self.meter["aabb"] = _box_aabb(self.meter["x"], self.meter["centre_y"], self.wall_z, self.meter["size"])
        gas = spec.get("gasMeter")
        self.gas = None
        if gas:
            self.gas = {
                "kind": "gas_meter",
                "x": float(gas["x"]),
                "centre_y": self.ground_y + float(gas["centreHeight"]),
                "size": [float(v) for v in gas["size"]],
            }
            self.gas["aabb"] = _box_aabb(self.gas["x"], self.gas["centre_y"], self.wall_z, self.gas["size"])
        self.windows = [
            {
                "x": float(w["x"]),
                "sill_y": self.ground_y + float(w["sillHeight"]),
                "w": float(w["size"][0]),
                "h": float(w["size"][1]),
            }
            for w in spec.get("windows", [])
        ]
        self.vents = [
            {
                "x": float(v["x"]),
                "centre_y": self.ground_y + float(v["centreHeight"]),
                "w": float(v["size"][0]),
                "h": float(v["size"][1]),
            }
            for v in spec.get("vents", [])
        ]
        self.wall_top = self.ground_y + self.wall_height

    def hazards(self) -> list[dict]:
        if not self.gas:
            return []
        centre = [
            round(self.gas["x"], 6),
            round(self.gas["centre_y"], 6),
            round(self.wall_z + self.gas["size"][2] / 2.0, 6),
        ]
        return [{"kind": "gas_meter", "centre": centre, "size": self.gas["size"], "aabb": self.gas["aabb"]}]


def _box_aabb(x: float, centre_y: float, wall_z: float, size: list[float]) -> dict:
    w, h, d = size
    return {
        "lo": [x - w / 2.0, centre_y - h / 2.0, wall_z],
        "hi": [x + w / 2.0, centre_y + h / 2.0, wall_z + d],
    }


# --------------------------------------------------------------------------------------
# Walk trajectory and camera poses
# --------------------------------------------------------------------------------------


def walk_x(t: float) -> float:
    return WALK_START_X + WALK_SPEED_MPS * t


def walk_y(t: float) -> float:
    return 0.03 * math.sin(2.0 * math.pi * t / 1.7)


def walk_z(t: float) -> float:
    # Distance from the wall is wall.distance - z(t), i.e. 1.55 .. 2.45 m.
    return 0.45 * math.sin(2.0 * math.pi * t / 6.4)


def aim_point(scene: Scene, t: float) -> np.ndarray:
    """The point on the wall the homeowner looks at: toward the meter, with walk lookahead."""
    x = walk_x(t)
    return np.array([scene.meter["x"] + 0.45 * (x - scene.meter["x"]), scene.ground_y + 1.1, scene.wall_z])


def camera_pose(position: np.ndarray, target: np.ndarray) -> np.ndarray:
    """ARKit camera-to-world matrix: +x right, +y up, looking along -z."""
    forward = target - position
    forward = forward / np.linalg.norm(forward)
    z_c = -forward
    up = np.array([0.0, 1.0, 0.0])
    x_c = np.cross(up, z_c)
    x_c = x_c / np.linalg.norm(x_c)
    y_c = np.cross(z_c, x_c)
    matrix = np.eye(4)
    matrix[:3, 0] = x_c
    matrix[:3, 1] = y_c
    matrix[:3, 2] = z_c
    matrix[:3, 3] = position
    return matrix


def pose_at(scene: Scene, t: float) -> np.ndarray:
    return camera_pose(np.array([walk_x(t), walk_y(t), walk_z(t)]), aim_point(scene, t))


def keyframe_times() -> list[float]:
    return [k * KEYFRAME_DT for k in range(N_KEYFRAMES)]


# --------------------------------------------------------------------------------------
# Ray casting
# --------------------------------------------------------------------------------------


def ray_grid(pose: np.ndarray, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    """Camera origin and one unit world-space direction per pixel centre.

    The depth grid shares the keyframe FOV with proportionally scaled
    intrinsics (fx * w/640, cx = w/2), the way ARKit reports a scaled plane.
    """
    fx = FX * w / KEYFRAME_W
    fy = FY * h / KEYFRAME_H
    cx = CX * w / KEYFRAME_W
    cy = CY * h / KEYFRAME_H
    u = np.arange(w, dtype=np.float64) + 0.5
    v = np.arange(h, dtype=np.float64) + 0.5
    uu, vv = np.meshgrid(u, v)
    dx = (uu - cx) / fx
    dy = -(vv - cy) / fy
    d_cam = np.stack([dx, dy, -np.ones_like(dx)], axis=-1)
    d_world = d_cam @ pose[:3, :3].T
    d_world /= np.linalg.norm(d_world, axis=-1, keepdims=True)
    return pose[:3, 3].copy(), d_world


def hit_plane_z(origin, d, z_plane, x0, x1, y0, y1):
    """Rectangular axis-aligned patch at z = z_plane. Returns (t, normal) with miss t = inf."""
    dz = d[..., 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (z_plane - origin[..., 2]) / dz
    px = origin[..., 0] + t * d[..., 0]
    py = origin[..., 1] + t * d[..., 1]
    ok = (t > 1e-6) & (px >= x0) & (px <= x1) & (py >= y0) & (py <= y1)
    t = np.where(ok & (dz != 0), t, np.inf)
    normal = np.zeros_like(d)
    normal[..., 2] = 1.0
    return t, normal


def hit_plane_y(origin, d, y_plane, x0, x1, z0, z1):
    dy = d[..., 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (y_plane - origin[..., 1]) / dy
    px = origin[..., 0] + t * d[..., 0]
    pz = origin[..., 2] + t * d[..., 2]
    ok = (t > 1e-6) & (px >= x0) & (px <= x1) & (pz >= z0) & (pz <= z1)
    t = np.where(ok & (dy != 0), t, np.inf)
    normal = np.zeros_like(d)
    normal[..., 1] = 1.0
    return t, normal


def hit_box(origin, d, lo, hi):
    """Axis-aligned box slab test. Returns (t, normal) with miss t = inf."""
    with np.errstate(divide="ignore", invalid="ignore"):
        t1 = (np.asarray(lo) - origin) / d
        t2 = (np.asarray(hi) - origin) / d
    t_near = np.minimum(t1, t2)  # (H, W, 3)
    t_far = np.maximum(t1, t2)
    t_min = t_near.max(axis=-1)
    t_max = t_far.min(axis=-1)
    ok = (t_max >= t_min) & (t_max > 0.0)
    t = np.where(ok & (t_min > 1e-6), t_min, np.inf)
    inside = ok & (t_min <= 1e-6)  # camera inside the box: not our scene, call it a miss
    t = np.where(inside, np.inf, t)
    axis = t_near.argmax(axis=-1)
    d_along = np.take_along_axis(d, axis[..., None], axis=-1)[..., 0]
    sign = np.sign(-d_along)
    normal = np.zeros_like(d)
    ii, jj = np.indices(axis.shape)
    normal[ii, jj, axis] = sign
    return t, normal


def cast_scene(scene: Scene, origin, d):
    """Nearest hit among ground, wall and the boxes. Returns (t, normal, surface_id)."""
    ts = []
    normals = []
    ids = []
    ground_x0, ground_x1 = scene.wall_x0 - 12.0, scene.wall_x1 + 12.0
    t, n = hit_plane_y(origin, d, scene.ground_y, ground_x0, ground_x1, scene.wall_z, scene.wall_z + 30.0)
    ts.append(t)
    normals.append(n)
    ids.append(np.full(t.shape, 1, dtype=np.int8))  # 1 = ground
    t, n = hit_plane_z(origin, d, scene.wall_z, scene.wall_x0, scene.wall_x1, scene.ground_y, scene.wall_top)
    ts.append(t)
    normals.append(n)
    ids.append(np.full(t.shape, 2, dtype=np.int8))  # 2 = wall
    for idx, box in enumerate([scene.meter, scene.gas], start=3):
        if box is None:
            continue
        aabb = box["aabb"]
        t, n = hit_box(origin, d, aabb["lo"], aabb["hi"])
        ts.append(t)
        normals.append(n)
        ids.append(np.full(t.shape, idx, dtype=np.int8))  # 3 = meter box, 4 = gas box
    t_all = np.stack(ts)
    nearest = t_all.argmin(axis=0)
    t_min = np.take_along_axis(t_all, nearest[None], axis=0)[0]
    normal = np.take_along_axis(np.stack(normals), np.broadcast_to(nearest, t_all.shape)[..., None], axis=0)[0]
    surface = np.take_along_axis(np.stack(ids), nearest[None], axis=0)[0]
    return t_min, normal, surface


# --------------------------------------------------------------------------------------
# Texture
# --------------------------------------------------------------------------------------


def _hash01(*grids: np.ndarray, salt: int = 0) -> np.ndarray:
    """Deterministic integer hash of quantised world positions, in [0, 1)."""
    h = np.full(grids[0].shape, np.int64(salt) * np.int64(974711) + np.int64(668265263), dtype=np.int64)
    for k, grid in enumerate(grids):
        h = h ^ np.int64((k + 1) * 374761393) * grid.astype(np.int64)
    h = h ^ (h >> np.int64(13))
    h = h * np.int64(1274126177)
    h = h ^ (h >> np.int64(16))
    return ((h % np.int64(4096)).astype(np.float64) + 0.5) / 4096.0


def _quant(position: np.ndarray, step: float) -> tuple[np.ndarray, ...]:
    return tuple(np.floor(position[..., k] / step).astype(np.int64) for k in range(3))


def shade(scene: Scene, t, normal, surface, origin, d, rng_seed: int) -> np.ndarray:
    """RGB per pixel. Textures are functions of the hit point, so they are view-independent."""
    point = origin + t[..., None] * d
    # Miss pixels carry t = inf; their coords become 0 here so the texture math
    # below never sees inf/nan (the surfaces never select those pixels anyway).
    point = np.where(np.isfinite(point), point, 0.0)
    image = np.zeros(t.shape + (3,), dtype=np.float64)
    ndl = np.clip((normal * SUN).sum(axis=-1), 0.0, 1.0)

    # Base per-surface albedo with multi-scale hash detail, then Lambert + ambient.
    for sid, base, coarse, fine in [
        (1, np.array([150.0, 148.0, 143.0]), 0.08, 0.03),  # concrete ground
        (2, np.array([172.0, 96.0, 72.0]), 0.08, 0.02),  # brick facade
        (3, np.array([196.0, 198.0, 192.0]), 0.05, 0.02),  # meter body
        (4, np.array([214.0, 178.0, 42.0]), 0.05, 0.02),  # gas meter body
    ]:
        mask = surface == sid
        if not mask.any():
            continue
        coarse_n = (_hash01(*_quant(point, coarse), salt=sid) - 0.5) * 22.0
        fine_n = (_hash01(*_quant(point, fine), salt=sid + 17) - 0.5) * 9.0
        albedo = base[None, None, :] + coarse_n[..., None] + fine_n[..., None]
        image[mask] = albedo[mask]

    # Brick pattern on the wall: running bond, mortar lines, per-brick variation.
    wall = surface == 2
    if wall.any():
        y = point[..., 1] - scene.ground_y
        x = point[..., 0] - scene.wall_x0
        row = np.floor(y / 0.075)
        xoff = (row % 2.0) * 0.1
        col = np.floor((x + xoff) / 0.20)
        fy = y / 0.075 - row
        fxx = (x + xoff) / 0.20 - col
        mortar = (fy < 0.13) | (fxx < 0.055)
        brick_var = (_hash01(row.astype(np.int64), col.astype(np.int64), salt=91) - 0.5) * 30.0
        mortar_color = np.array([186.0, 184.0, 178.0])
        yy, xx = np.indices(t.shape)
        sel = yy[wall], xx[wall]
        base = image[sel]
        base = np.where(mortar[wall][:, None], mortar_color[None, :], base + brick_var[wall][:, None])
        # Sun gradient down the wall: lighter near the top, slight dirt near the ground.
        lift = 26.0 * ((point[..., 1][wall] - scene.ground_y) / scene.wall_height - 0.5)
        image[sel] = base + lift[:, None]

    # Meter face: dark frame, a dial of rings, small hash texture.
    _paint_meter_face(image, surface, point, scene.meter, dial=True)
    if scene.gas is not None:
        _paint_gas_face(image, surface, point, scene.gas)

    # Windows and vents are painted onto the wall surface (depth still sees the wall).
    for win in scene.windows:
        inside = (
            wall
            & (np.abs(point[..., 0] - win["x"]) <= win["w"] / 2.0)
            & (point[..., 1] >= win["sill_y"])
            & (point[..., 1] <= win["sill_y"] + win["h"])
        )
        if not inside.any():
            continue
        px = point[..., 0]
        py = point[..., 1]
        frame = (
            (np.abs(px - win["x"]) > win["w"] / 2.0 - 0.06)
            | (py > win["sill_y"] + win["h"] - 0.06)
            | (py < win["sill_y"] + 0.06)
        )
        glass = np.array([76.0, 94.0, 116.0])
        sheen = 42.0 * np.clip(np.sin((px + py) * 9.0), 0.0, 1.0) ** 2
        color = glass[None, None, :] + sheen[..., None]
        color = np.where(frame[..., None], np.array([233.0, 231.0, 226.0])[None, None, :], color)
        image[inside] = color[inside]
    for vent in scene.vents:
        inside = (
            wall
            & (np.abs(point[..., 0] - vent["x"]) <= vent["w"] / 2.0)
            & (np.abs(point[..., 1] - vent["centre_y"]) <= vent["h"] / 2.0)
        )
        if not inside.any():
            continue
        py = point[..., 1]
        slat = ((py / 0.025) % 1.0) < 0.5
        color = np.where(
            slat[..., None], np.array([52.0, 52.0, 54.0])[None, None, :], np.array([72.0, 72.0, 74.0])[None, None, :]
        )
        image[inside] = color[inside]

    # Lambert shading everywhere but the painted overlays already include their own contrast.
    lit = image * (0.55 + 0.45 * ndl[..., None])
    image = np.where((surface > 0)[..., None], lit, image)

    # Sensor noise, fixed per frame: keeps JPEGs from flattening and mirrors a real camera.
    noise = np.random.default_rng(rng_seed).integers(-2, 3, size=image.shape, dtype=np.int16)
    image = image + noise
    return np.clip(image, 0, 255).astype(np.uint8)


def _paint_meter_face(image, surface, point, box, dial: bool) -> None:
    front = (surface == 3) & (np.abs(point[..., 2] - (box["aabb"]["hi"][2])) < 1e-6)
    if not front.any():
        return
    w, h, _ = box["size"]
    u = (point[..., 0] - (box["x"] - w / 2.0)) / w
    v = (point[..., 1] - (box["aabb"]["lo"][1])) / h
    border = ((u < 0.06) | (u > 0.94) | (v < 0.05) | (v > 0.95))[front]
    color = image[front]
    frame = np.array([58.0, 60.0, 62.0])
    color = np.where(border[:, None], frame[None, :], color)
    if dial:
        cx, cy = 0.5, 0.42
        r = np.hypot((u[front] - cx) * w, (v[front] - cy) * h)
        ring = (r % 0.022) < 0.011
        cap = r < 0.012
        dark = np.array([70.0, 72.0, 74.0])
        light = np.array([168.0, 170.0, 166.0])
        dial_color = np.where((ring | cap)[:, None], dark[None, :], light[None, :])
        body = ~border
        color = np.where(body[:, None] & (r < 0.09)[:, None], dial_color, color)
    image[front] = color


def _paint_gas_face(image, surface, point, box) -> None:
    front = (surface == 4) & (np.abs(point[..., 2] - (box["aabb"]["hi"][2])) < 1e-6)
    if not front.any():
        return
    w, h, _ = box["size"]
    u = (point[..., 0] - (box["x"] - w / 2.0)) / w
    v = (point[..., 1] - (box["aabb"]["lo"][1])) / h
    border = ((u < 0.05) | (u > 0.95) | (v < 0.05) | (v > 0.95))[front]
    grille = (((v[front] / 0.06) % 1.0) < 0.4) & ~border
    dark = np.array([70.0, 62.0, 24.0])
    color = image[front]
    color = np.where(border[:, None], np.array([74.0, 70.0, 52.0])[None, :], color)
    color = np.where((~border & grille)[:, None], dark[None, :], color)
    image[front] = color


# --------------------------------------------------------------------------------------
# Frame rendering
# --------------------------------------------------------------------------------------


def render_frame(
    scene: Scene, pose: np.ndarray, frame_index: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """RGB (640x480x3), depth f32 metres (192x256), confidence u8, feature points."""
    origin, d = ray_grid(pose, KEYFRAME_W, KEYFRAME_H)
    t, normal, surface = cast_scene(scene, origin, d)
    rgb = shade(scene, t, normal, surface, origin, d, rng_seed=[scene.seed, 1000 + frame_index])

    d_origin, d_dir = ray_grid(pose, DEPTH_W, DEPTH_H)
    dt, _normal, _surface = cast_scene(scene, d_origin, d_dir)
    depth = dt.astype("<f4")
    confidence = np.where(np.isfinite(depth), np.where(depth <= 6.0, 3, 2).astype(np.uint8), np.uint8(0))
    features = _features(rgb, t, frame_index, scene.seed)
    return rgb, depth, confidence, features


def _features(rgb, t, frame_index, seed) -> list[tuple[float, float, float, float, float]]:
    """Deterministic 20-byte feature rows: u, v, response, scale, angle (float32 LE)."""
    h, w = t.shape
    rows = np.arange(FEATURES_STRIDE_PX, h - FEATURES_STRIDE_PX, FEATURES_STRIDE_PX)
    cols = np.arange(FEATURES_STRIDE_PX, w - FEATURES_STRIDE_PX, FEATURES_STRIDE_PX)
    rr, cc = np.meshgrid(rows, cols)
    depth = t[rr, cc]
    valid = np.isfinite(depth)
    lum = rgb[rr, cc].astype(np.float64).mean(axis=-1)
    response = np.abs(lum - 118.0) * valid
    order = np.argsort(-response, axis=None)[:FEATURES_PER_FRAME]
    rng = np.random.default_rng([seed, 7000 + frame_index])
    points = []
    for flat in order:
        r, c = np.unravel_index(flat, response.shape)
        if not valid[r, c]:
            continue
        points.append(
            (
                float(cc[r, c] + 0.5),
                float(rr[r, c] + 0.5),
                float(response[r, c]),
                float(rng.uniform(2.0, 5.0)),
                0.0,
            )
        )
    return points


# --------------------------------------------------------------------------------------
# Streams
# --------------------------------------------------------------------------------------


def _device_gravity(pose: np.ndarray) -> np.ndarray:
    """Gravity in Core Motion device frame, exactly as server.packet.poses.check_gravity_stream expects."""
    return DEVICE_FROM_CAMERA @ (pose[:3, :3].T @ np.array([0.0, -1.0, 0.0]))


def _device_from_world(pose: np.ndarray) -> np.ndarray:
    return pose[:3, :3] @ DEVICE_FROM_CAMERA.T


def _pose_interp(scene: Scene, t: float) -> np.ndarray:
    return pose_at(scene, t)


def _csv_float(value: float, digits: int = 6) -> str:
    return f"{value:.{digits}f}"


def _write_gzip_csv(path: Path, lines: list[str]) -> None:
    with open(path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as gz:
            gz.write(("\n".join(lines) + "\n").encode())


def build_streams(scene: Scene, packet_dir: Path) -> dict:
    """motion/heading/location/imuRaw at 100 Hz (location 1 Hz) and arkitPoses at 30 Hz."""
    streams_dir = packet_dir / "streams"
    streams_dir.mkdir(parents=True, exist_ok=True)
    t0 = SESSION_START_UPTIME

    # --- motion (100 Hz) ---
    lines = ["t,qx,qy,qz,qw,rotX,rotY,rotZ,gravX,gravY,gravZ,accX,accY,accZ,magX,magY,magZ,magAccuracy,heading"]
    steps = int(round(WALK_SECONDS + 3.5) * 100)
    rng = np.random.default_rng([scene.seed, 11])
    acc_noise = rng.uniform(-0.004, 0.004, size=(steps, 3))
    for k in range(steps + 1):
        t = k / 100.0
        if t > WALK_SECONDS + 3.5:
            break
        pose = _pose_interp(scene, t)
        quat = rotmat_to_quat_xyzw(_device_from_world(pose))
        grav = _device_gravity(pose)
        yaw_prev = _yaw(_pose_interp(scene, max(t - 0.005, 0.0)))
        yaw_next = _yaw(_pose_interp(scene, min(t + 0.005, WALK_SECONDS)))
        rot = [0.004, (yaw_next - yaw_prev) / 0.01, 0.002]
        acc = grav + acc_noise[min(k, steps - 1)]
        mag = _device_from_world(pose).T @ np.array([0.0, 0.0, -45.0])
        rows = [f"{t0 + t:.3f}"]
        rows += [_csv_float(v, 6) for v in quat]
        rows += [_csv_float(v, 4) for v in rot]
        rows += [_csv_float(v, 6) for v in grav]
        rows += [_csv_float(v, 6) for v in acc]
        rows += [_csv_float(v, 4) for v in mag]
        rows += ["45.0", "132.4"]
        lines.append(",".join(rows))
    _write_gzip_csv(streams_dir / "motion.csv.gz", lines)

    # --- imuRaw (100 Hz) ---
    lines = ["t,ax,ay,az,gx,gy,gz"]
    for k in range(steps + 1):
        t = k / 100.0
        if t > WALK_SECONDS + 3.5:
            break
        pose = _pose_interp(scene, t)
        grav = _device_gravity(pose)
        acc = grav + acc_noise[min(k, steps - 1)]
        yaw_prev = _yaw(_pose_interp(scene, max(t - 0.005, 0.0)))
        yaw_next = _yaw(_pose_interp(scene, min(t + 0.005, WALK_SECONDS)))
        rows = [f"{t0 + t:.3f}"]
        rows += [_csv_float(v, 6) for v in acc]
        rows += [_csv_float(v, 4) for v in [0.004, (yaw_next - yaw_prev) / 0.01, 0.002]]
        lines.append(",".join(rows))
    _write_gzip_csv(streams_dir / "imuRaw.csv.gz", lines)

    # --- arkitPoses (30 Hz, keyframe times sit exactly on this grid) ---
    lines = ["t,epoch,tracking,tx,ty,tz,qx,qy,qz,qw,fx,fy,cx,cy"]
    n_poses = int(round((WALK_SECONDS + 3.5) * 30))
    for k in range(n_poses + 1):
        t = k / 30.0
        pose = _pose_interp(scene, t)
        quat = rotmat_to_quat_xyzw(_device_from_world(pose))
        rows = [f"{t0 + t:.3f}", "e1", "normal"]
        rows += [_csv_float(v, 6) for v in pose[:3, 3]]
        rows += [_csv_float(v, 6) for v in quat]
        rows += [_csv_float(FX, 3), _csv_float(FY, 3), _csv_float(CX, 3), _csv_float(CY, 3)]
        lines.append(",".join(rows))
    _write_gzip_csv(streams_dir / "arkitPoses.csv.gz", lines)

    # --- heading (10 Hz) ---
    lines = ["t,timestamp,magneticHeading,trueHeading,headingAccuracy,x,y,z"]
    for k in range(int((WALK_SECONDS + 3.5) * 10) + 1):
        t = k / 10.0
        stamp = f"2026-10-16T14:00:{int(t):02d}.{int(round((t % 1) * 100)):02d}"
        rows = [f"{t0 + t:.3f}", stamp, "132.4", "131.9", "12.0", "24.1", "-3.9", "-41.7"]
        lines.append(",".join(rows))
    _write_gzip_csv(streams_dir / "heading.csv.gz", lines)

    # --- location (1 Hz) ---
    lines = [
        "t,timestamp,latitude,longitude,altitude,ellipsoidalAltitude,"
        "horizontalAccuracy,verticalAccuracy,course,courseAccuracy,speed,speedAccuracy"
    ]
    for k in range(int(WALK_SECONDS + 3.5) + 1):
        t = k * 1.0
        speed = WALK_SPEED_MPS if t <= WALK_SECONDS else 0.0
        stamp = f"2026-10-16T14:00:{int(t):02d}.00"
        rows = [
            f"{t0 + t:.3f}",
            stamp,
            "30.267200",
            "-97.743100",
            "149.0",
            "174.2",
            "5.0",
            "3.0",
            "90.0",
            "10.0",
            _csv_float(speed, 3),
            "0.5",
        ]
        lines.append(",".join(rows))
    _write_gzip_csv(streams_dir / "location.csv.gz", lines)

    return {
        "motion": {
            "file": "streams/motion.csv.gz",
            "rateHz": 100,
            "referenceFrame": "xArbitraryZVertical",
            "columns": [
                "t",
                "qx",
                "qy",
                "qz",
                "qw",
                "rotX",
                "rotY",
                "rotZ",
                "gravX",
                "gravY",
                "gravZ",
                "accX",
                "accY",
                "accZ",
                "magX",
                "magY",
                "magZ",
                "magAccuracy",
                "heading",
            ],
        },
        "heading": {
            "file": "streams/heading.csv.gz",
            "headingOrientation": "portrait",
            "headingFilter": 1.0,
            "columns": ["t", "timestamp", "magneticHeading", "trueHeading", "headingAccuracy", "x", "y", "z"],
        },
        "location": {
            "file": "streams/location.csv.gz",
            "desiredAccuracy": "best",
            "columns": [
                "t",
                "timestamp",
                "latitude",
                "longitude",
                "altitude",
                "ellipsoidalAltitude",
                "horizontalAccuracy",
                "verticalAccuracy",
                "course",
                "courseAccuracy",
                "speed",
                "speedAccuracy",
            ],
        },
        "imuRaw": {
            "file": "streams/imuRaw.csv.gz",
            "rateHz": 100,
            "columns": ["t", "ax", "ay", "az", "gx", "gy", "gz"],
        },
        "arkitPoses": {
            "file": "streams/arkitPoses.csv.gz",
            "rateHz": 30,
            "columns": ["t", "epoch", "tracking", "tx", "ty", "tz", "qx", "qy", "qz", "qw", "fx", "fy", "cx", "cy"],
        },
    }


def _yaw(pose: np.ndarray) -> float:
    forward = -pose[:3, 2]
    return math.atan2(float(forward[0]), -float(forward[2]))


# --------------------------------------------------------------------------------------
# Packet assembly
# --------------------------------------------------------------------------------------


def _round_pose(pose: np.ndarray) -> list[float]:
    return [round(float(v), 9) for v in column_major(pose)]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_entry(path: Path, root: Path, role: str) -> dict:
    data = path.read_bytes()
    return {"path": str(path.relative_to(root)), "bytes": len(data), "sha256": _sha256(data), "role": role}


def still_views(scene: Scene) -> list[dict]:
    """The meter close-up (head-on) and the oblique still the scale reference needs."""
    meter_c = np.array([scene.meter["x"], scene.meter["centre_y"], scene.wall_z + scene.meter["size"][2] / 2.0])
    close_pos = np.array([scene.meter["x"], scene.meter["centre_y"], scene.wall_z + 0.55])
    oblique_pos = np.array([scene.meter["x"] + 0.38, scene.meter["centre_y"] + 0.12, scene.wall_z + 0.52])

    def angle_deg(pos: np.ndarray) -> float:
        view = meter_c - pos
        view = view / np.linalg.norm(view)
        # Angle between the camera's viewing axis and the wall plane (0 = head-on):
        # the wall normal points out of the wall toward the camera, so use |dot|.
        return math.degrees(math.acos(min(1.0, abs(float(view[2])))))

    return [
        {
            "id": "s_meter_close",
            "purpose": "meter_close",
            "pos": close_pos,
            "target": meter_c,
            "viewAngleDeg": angle_deg(close_pos),
            "timestamp": SESSION_START_UPTIME + WALK_SECONDS + 1.5,
        },
        {
            "id": "s_meter_oblique",
            "purpose": "meter_oblique",
            "pos": oblique_pos,
            "target": meter_c,
            "viewAngleDeg": angle_deg(oblique_pos),
            "timestamp": SESSION_START_UPTIME + WALK_SECONDS + 2.5,
        },
    ]


def generate_packet(spec: dict, out_dir: Path) -> dict:
    """Render the packet into out_dir. Returns a summary dict (paths, counts, packet id)."""
    scene = Scene(spec)
    out_dir = Path(out_dir)
    (out_dir / "keyframes").mkdir(parents=True, exist_ok=True)
    (out_dir / "stills").mkdir(parents=True, exist_ok=True)

    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"gen_app_capture/{scene.name}/{scene.seed}")
    packet_id = digest.hex
    session_id = uuid.uuid5(uuid.NAMESPACE_URL, f"gen_app_capture/{scene.name}/{scene.seed}/session").hex.upper()

    frames = []
    files = []
    for index, t in enumerate(keyframe_times()):
        pose = pose_at(scene, t)
        rgb, depth, confidence, features = render_frame(scene, pose, index)
        kid = f"k{index:05d}"
        jpg = out_dir / "keyframes" / f"{kid}.jpg"
        Image.fromarray(rgb).save(jpg, format="JPEG", quality=JPEG_QUALITY, subsampling=0)
        depth_path = out_dir / "keyframes" / f"{kid}.depth.f32"
        depth.tofile(depth_path)
        conf_path = out_dir / "keyframes" / f"{kid}.confidence.u8"
        confidence.tofile(conf_path)
        feat_path = out_dir / "keyframes" / f"{kid}.features.bin"
        feat_path.write_bytes(b"".join(np.asarray(p, dtype="<f4").tobytes() for p in features))
        files += [
            _file_entry(jpg, out_dir, "keyframe"),
            _file_entry(depth_path, out_dir, "depth"),
            _file_entry(conf_path, out_dir, "confidence"),
            _file_entry(feat_path, out_dir, "features"),
        ]
        frames.append(
            {
                "id": kid,
                "img": f"keyframes/{kid}.jpg",
                "w": KEYFRAME_W,
                "h": KEYFRAME_H,
                "intrinsics": [round(v, 3) for v in INTRINSICS],
                "pose": _round_pose(pose),
                "timestamp": round(SESSION_START_UPTIME + t, 4),
                "tracking": "normal",
                "epoch": "e1",
                "reason": "motion",
                "exposure": {"duration": 0.008, "offset": 0.0, "iso": 50, "fNumber": 1.78},
                "exif": {
                    "Make": "Apple",
                    "Model": "iPhone 15 Pro",
                    "LensModel": "iPhone 15 Pro back dual wide camera 6.765mm f/1.78",
                    "FocalLength": 6.765,
                    "FocalLenIn35mmFilm": 26,
                    "PixelXDimension": KEYFRAME_W,
                    "PixelYDimension": KEYFRAME_H,
                    "ExposureTime": 0.008,
                    "ISOSpeedRatings": [50],
                    "FNumber": 1.78,
                    "BrightnessValue": 7.4,
                    "ExposureBiasValue": 0.0,
                    "DateTimeOriginal": "2026:10:16 09:00:00",
                    "SubsecTimeOriginal": f"{index * 33 % 1000:03d}",
                    "OffsetTimeOriginal": "-05:00",
                },
                "lightEstimate": {"ambientIntensity": 980.0, "ambientColorTemperature": 6500.0},
                "features": {"file": f"keyframes/{kid}.features.bin", "count": len(features)},
                "depth": {
                    "file": f"keyframes/{kid}.depth.f32",
                    "confidenceFile": f"keyframes/{kid}.confidence.u8",
                    "w": DEPTH_W,
                    "h": DEPTH_H,
                    "smoothed": False,
                },
            }
        )

    # --- stills ---
    stills = []
    views = still_views(scene)
    for view in views:
        pose = camera_pose(view["pos"], view["target"])
        rgb, depth, confidence, features = render_frame(scene, pose, 100 + len(stills))
        img = out_dir / "stills" / f"{view['id']}.jpg"
        Image.fromarray(rgb).save(img, format="JPEG", quality=JPEG_QUALITY, subsampling=0)
        files.append(_file_entry(img, out_dir, "still"))
        link = frames[min(range(len(frames)), key=lambda i: abs(walk_x(keyframe_times()[i]) - scene.meter["x"]))]["id"]
        stills.append(
            {
                "id": view["id"],
                "purpose": view["purpose"],
                "img": f"stills/{view['id']}.jpg",
                "w": KEYFRAME_W,
                "h": KEYFRAME_H,
                "timestamp": round(view["timestamp"], 4),
                "orientation": 1,
                "keyframe": link,
                "intrinsics": [round(v, 3) for v in INTRINSICS],
                "gravity": [round(float(v), 6) for v in _device_gravity(pose)],
            }
        )

    # --- streams ---
    streams = build_streams(scene, out_dir)
    for name in streams:
        files.append(_file_entry(out_dir / streams[name]["file"], out_dir, "stream"))
    files.sort(key=lambda entry: entry["path"])

    # --- the confirmed meter tap: ray from the packet's own conventions, hit on the meter box ---
    tap_index = min(range(N_KEYFRAMES), key=lambda i: abs(walk_x(keyframe_times()[i]) - scene.meter["x"]))
    frame = frames[tap_index]
    pose = from_column_major(frame["pose"])
    meter_c = np.array([scene.meter["x"], scene.meter["centre_y"], scene.meter["aabb"]["hi"][2]])
    uv, _depth = project_arkit(pose, [FX, FY, CX, CY], meter_c[None, :])
    u, v = uv[0]
    pixel = [round(float(u), 3), round(float(v), 3)]
    origin, direction = pixel_ray_arkit(pose, [FX, FY, CX, CY], np.asarray(pixel))
    origin_r = [round(float(v0), 9) for v0 in origin]
    direction_r = [round(float(v0), 9) for v0 in direction]
    hit_pos, hit_distance = _ray_box_hit(np.asarray(origin_r), np.asarray(direction_r), scene.meter["aabb"])
    tap_time = round(frame["timestamp"] + 0.05, 4)
    taps = [
        {
            "id": "t1",
            "time": tap_time,
            "label": "meter",
            "tool": "mark",
            "step": "single",
            "keyframe": frame["id"],
            "frozen": False,
            "pixel": pixel,
            "rayOrigin": origin_r,
            "rayDirection": direction_r,
            "hit": {
                "position": [round(float(v0), 6) for v0 in hit_pos],
                "target": "sceneDepth",
                "alignment": "any",
                "distance": round(float(hit_distance), 4),
            },
            "confirmedByUser": True,
            "epoch": "e1",
        }
    ]

    planes = _planes(scene)

    packet = {
        "format": "house-capture-packet",
        "formatVersion": "0.4",
        "packetId": packet_id,
        "createdAt": "2026-10-16T14:00:13.000Z",
        "units": UNITS,
        "conventions": CONVENTIONS,
        "source": {
            "kind": "synthetic",
            "generator": "backend/scripts/gen_app_capture.py",
            "variant": "arkit-lidar",
            "noiseProfile": "none_ground_truth",
            "synthesizedFields": NSYNTH_FIELDS,
            "notes": (
                f"ground-truth packet generated from scene spec '{scene.name}' (seed {scene.seed}); "
                "poses and depth are exact, no drift or sensor noise beyond the render"
            ),
        },
        "session": {
            "id": session_id,
            "startedAt": "2026-10-16T13:59:41.000Z",
            "startedAtUptime": SESSION_START_UPTIME,
            "endedAtUptime": round(SESSION_START_UPTIME + WALK_SECONDS + 3.5, 4),
            "timeZone": "America/Chicago",
            "appVersion": "HouseScan 1.0 (gen_app_capture)",
            "deviceModel": "iPhone16,1",
            "systemVersion": "26.0",
            "tier": "arkit_lidar",
            "lidarAvailable": True,
            "sceneDepthEnabled": True,
            "meshReconstructionSupported": True,
            "sceneReconstruction": "none",
            "worldAlignment": "gravity",
            "planeDetection": ["horizontal", "vertical"],
            "videoFormat": {"imageResolution": [KEYFRAME_W, KEYFRAME_H], "framesPerSecond": 30},
            "locationAuthorization": "fullAccuracy",
            "motionReferenceFrame": "xArbitraryZVertical",
            "gates": SESSION_GATES,
        },
        "epochs": [{"id": "e1", "startTime": SESSION_START_UPTIME, "reason": "sessionStart"}],
        "keyframes": frames,
        "stills": stills,
        "taps": taps,
        "tracking": [
            {"time": SESSION_START_UPTIME, "state": "limited.initializing", "epoch": "e1"},
            {"time": round(SESSION_START_UPTIME + 1.2, 4), "state": "normal", "epoch": "e1"},
        ],
        "planes": planes,
        "streams": streams,
        "scaleReference": {
            "meterCloseUp": {
                "captured": True,
                "still": "s_meter_close",
                "arkitDistanceM": round(float(np.linalg.norm(views[0]["pos"] - meter_c)), 4),
                "viewAngleDeg": round(views[0]["viewAngleDeg"], 2),
                "nameplateVisible": True,
            },
            "meterIdentity": {"source": "not_read"},
            "ext": {
                "method": "a9-meter-model",
                "obliqueCloseUp": {
                    "captured": True,
                    "still": "s_meter_oblique",
                    "arkitDistanceM": round(float(np.linalg.norm(views[1]["pos"] - meter_c)), 4),
                    "viewAngleDeg": round(views[1]["viewAngleDeg"], 2),
                    "side": "left",
                    "nameplateVisible": False,
                },
                "doorViews": [],
                "doorOnMeterSide": None,
                "trueDepthScan": {"captured": False},
            },
        },
        "files": files,
    }
    packet_text = json.dumps(packet, indent=2) + "\n"
    (out_dir / "packet.json").write_text(packet_text)

    ground_truth = build_ground_truth(scene, packet, tap_pixel=pixel, hit_distance=float(hit_distance))
    (out_dir / "ground_truth.json").write_text(json.dumps(ground_truth, indent=2) + "\n")

    return {
        "packetDir": str(out_dir),
        "packetId": packet_id,
        "keyframes": len(frames),
        "stills": len(stills),
        "files": len(files),
        "tapPixel": pixel,
        "tapHitDistanceM": round(float(hit_distance), 4),
    }


def _ray_box_hit(origin: np.ndarray, direction: np.ndarray, aabb: dict) -> tuple[np.ndarray, float]:
    lo, hi = np.asarray(aabb["lo"]), np.asarray(aabb["hi"])
    with np.errstate(divide="ignore", invalid="ignore"):
        t1 = (lo - origin) / direction
        t2 = (hi - origin) / direction
    t_near = np.minimum(t1, t2).max()
    t_far = np.maximum(t1, t2).min()
    if not (t_far >= max(t_near, 0.0)):
        raise ValueError("tap ray misses the meter box")
    t = max(t_near, 0.0)
    position = origin + t * direction
    return position, float(np.linalg.norm(position - origin))


def _planes(scene: Scene) -> list[dict]:
    cx = (scene.wall_x0 + scene.wall_x1) / 2.0
    cy = scene.ground_y + scene.wall_height / 2.0
    width = scene.wall_x1 - scene.wall_x0
    wall_boundary = [
        [round(scene.wall_x0 - cx, 4), round(scene.ground_y - cy, 4)],
        [round(scene.wall_x1 - cx, 4), round(scene.ground_y - cy, 4)],
        [round(scene.wall_x1 - cx, 4), round(scene.wall_top - cy, 4)],
        [round(scene.wall_x0 - cx, 4), round(scene.wall_top - cy, 4)],
    ]
    wall = {
        "id": "WALL-0001",
        "epoch": "e1",
        "alignment": "vertical",
        "classification": "wall",
        "transform": _round_pose(
            np.array(
                [
                    [1.0, 0.0, 0.0, cx],
                    [0.0, 1.0, 0.0, cy],
                    [0.0, 0.0, 1.0, scene.wall_z],
                    [0.0, 0.0, 0.0, 1.0],
                ]
            )
        ),
        "center": [round(cx, 4), round(cy, 4), round(scene.wall_z, 4)],
        "planeExtent": {"width": round(width, 4), "height": round(scene.wall_height, 4), "rotationOnYAxis": 0.0},
        "boundary": wall_boundary,
        "sourceInliers": 48000,
    }
    floor = {
        "id": "FLOOR-0001",
        "epoch": "e1",
        "alignment": "horizontal",
        "classification": "floor",
        "transform": _round_pose(
            np.array(
                [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, scene.ground_y],
                    [0.0, -1.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ]
            )
        ),
        "center": [0.0, round(scene.ground_y, 4), 0.0],
        "planeExtent": {"width": 24.0, "height": 30.0, "rotationOnYAxis": 0.0},
        "boundary": [[-12.0, -15.0], [12.0, -15.0], [12.0, 15.0], [-12.0, 15.0]],
        "sourceInliers": 51000,
    }
    return [wall, floor]


def build_ground_truth(scene: Scene, packet: dict, tap_pixel: list[float], hit_distance: float) -> dict:
    """The facts the pipeline is supposed to recover, in the packet's own world frame."""
    meter_centre = [
        round(scene.meter["x"], 6),
        round(scene.meter["centre_y"], 6),
        round(scene.wall_z + scene.meter["size"][2] / 2.0, 6),
    ]
    clearances: dict = {"note": "horizontal distances on the wall plane, metres; no rule values live here"}
    hazards = scene.hazards()
    if scene.gas is not None:
        clearances["meterCentreToGasCentreM"] = round(abs(scene.meter["x"] - scene.gas["x"]), 6)
        meter_left_edge = scene.meter["x"] - scene.meter["size"][0] / 2.0
        gas_right_edge = scene.gas["x"] + scene.gas["size"][0] / 2.0
        clearances["meterEdgeToGasEdgeM"] = round(meter_left_edge - gas_right_edge, 6)
    tap = packet["taps"][0]
    return {
        "scene": scene.name,
        "seed": scene.seed,
        "generator": "backend/scripts/gen_app_capture.py",
        "world": "packet.json conventions: ARKit .gravity, y up, origin at the device position at session start",
        "units": {"length": "meters", "angle": "degrees"},
        "wall": {
            "plane": {"point": [0.0, 0.0, round(scene.wall_z, 6)], "normal": [0.0, 0.0, 1.0]},
            "xRange": [scene.wall_x0, scene.wall_x1],
            "bottomY": round(scene.ground_y, 6),
            "height": round(scene.wall_height, 6),
        },
        "meter": {
            "kind": "electric",
            "centre": meter_centre,
            "size": scene.meter["size"],
            "frontFaceZ": round(scene.meter["aabb"]["hi"][2], 6),
            "aabb": scene.meter["aabb"],
        },
        "hazards": hazards,
        "clearances": clearances,
        "windows": [
            {
                "centre": [round(w["x"], 6), round(w["sill_y"] + w["h"] / 2.0, 6)],
                "size": [w["w"], w["h"]],
                "sillY": round(w["sill_y"], 6),
            }
            for w in scene.windows
        ],
        "vents": [
            {
                "centre": [round(v["x"], 6), round(v["centre_y"], 6)],
                "size": [v["w"], v["h"]],
            }
            for v in scene.vents
        ],
        "capture": {
            "keyframes": len(packet["keyframes"]),
            "keyframeResolution": [KEYFRAME_W, KEYFRAME_H],
            "depthResolution": [DEPTH_W, DEPTH_H],
            "walkDistanceToWallM": [1.5, 2.5],
            "tap": {
                "keyframe": tap["keyframe"],
                "pixel": tap_pixel,
                "hitPosition": tap["hit"]["position"],
                "hitDistanceM": round(hit_distance, 4),
            },
        },
    }


def load_spec(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", required=True, type=Path, help="scene spec JSON")
    parser.add_argument("--out", required=True, type=Path, help="packet output directory")
    args = parser.parse_args(argv)
    summary = generate_packet(load_spec(args.spec), args.out)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
