"""Synthesize the phone's other sensors from the clean trajectory.

Everything here is synthetic and listed in the packet's `source.synthesizedFields`. The clean
trajectory is known only at the photos, so between photos it is interpolated (PCHIP positions,
rotation spline), and a walking gait is added to the accelerometer.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.spatial import ConvexHull
from scipy.spatial.transform import Rotation, RotationSpline

from .align import ransac_planes
from .conventions import (
    DEVICE_FROM_CAMERA,
    heading_deg_of_vector,
    log_so3,
    rot_y,
    rotmat_to_quat_xyzw,
)
from .noise import Profile

G = 9.80665
WALK_SPEED_M_S = 0.9
TURN_RATE_DEG_S = 40.0
MIN_GAP_S = 0.4

# Austin, TX: magnetic declination about +2.4 deg (east), field ~46 uT at ~58 deg inclination,
# geoid undulation ~ -26 m (ellipsoidal height = MSL height + N).
DEFAULT_ANCHOR = {"latitude": 30.2672, "longitude": -97.7431, "altitude": 150.0, "declination_deg": 2.4,
                  "field_uT": 46.0, "inclination_deg": 58.0, "geoid_undulation_m": -26.4, "timeZone": "America/Chicago"}

MOTION_COLUMNS = ["t", "qx", "qy", "qz", "qw", "rotX", "rotY", "rotZ", "gravX", "gravY", "gravZ",
                  "accX", "accY", "accZ", "magX", "magY", "magZ", "magAccuracy", "heading"]
HEADING_COLUMNS = ["t", "timestamp", "magneticHeading", "trueHeading", "headingAccuracy", "x", "y", "z"]
LOCATION_COLUMNS = ["t", "timestamp", "latitude", "longitude", "altitude", "ellipsoidalAltitude",
                    "horizontalAccuracy", "verticalAccuracy", "course", "courseAccuracy", "speed", "speedAccuracy"]


def synth_timestamps(c: np.ndarray, R: np.ndarray, rng: np.random.Generator, t0: float) -> np.ndarray:
    """A person walking between shots: time from distance, turn angle and a pause."""
    t = [t0]
    for i in range(1, len(c)):
        d = float(np.linalg.norm(c[i] - c[i - 1]))
        ang = math.degrees(np.linalg.norm(log_so3(R[i - 1].T @ R[i])))
        gap = max(d / WALK_SPEED_M_S, ang / TURN_RATE_DEG_S, MIN_GAP_S) + rng.exponential(0.3)
        t.append(t[-1] + gap)
    return np.array(t)


def tracking_states(times, R, epochs, reset_index, n_features, profile: Profile, rng) -> list[str]:
    """ARCamera.TrackingState per keyframe, in PR #7's manifest spelling."""
    states = []
    epoch_start = {}
    for i, e in enumerate(epochs):
        epoch_start.setdefault(e, times[i])
    for i in range(len(times)):
        s = "normal"
        if times[i] - epoch_start[epochs[i]] < profile.init_seconds:
            s = "limited.initializing"
        elif i > 0:
            rate = math.degrees(np.linalg.norm(log_so3(R[i - 1].T @ R[i]))) / max(times[i] - times[i - 1], 1e-3)
            if rate > profile.excessive_motion_deg_per_s:
                s = "limited.excessiveMotion"
            elif n_features[i] < profile.insufficient_features_min:
                s = "limited.insufficientFeatures"
            elif rng.random() < 0.02:
                s = "limited.excessiveMotion"
        if reset_index is not None and i == reset_index - 1:
            s = "limited.relocalizing"
        states.append(s)
    return states


def tracking_events(times, states, epochs) -> list[dict]:
    out, prev = [], None
    for t, s, e in zip(times, states, epochs):
        if s != prev:
            out.append({"time": round(float(t), 4), "state": s, "epoch": e})
            prev = s
    return out


class Trajectory:
    """Continuous clean camera trajectory through the keyframes (ARKit world, camera-to-world)."""

    def __init__(self, times: np.ndarray, c: np.ndarray, R: np.ndarray):
        self.t0, self.t1 = float(times[0]), float(times[-1])
        self.pos = PchipInterpolator(times, c, axis=0, extrapolate=True)
        self.vel = self.pos.derivative(1)
        self.acc = self.pos.derivative(2)
        rots = Rotation.from_matrix(R)
        self.rot = RotationSpline(times, rots) if len(times) > 2 else None
        self._R = R
        self._times = times

    def R(self, t: np.ndarray) -> np.ndarray:
        t = np.clip(t, self.t0, self.t1)
        if self.rot is None:
            return np.repeat(self._R[:1], len(t), axis=0)
        return self.rot(t).as_matrix()


def enu_from_arkit(p: np.ndarray, north_az_deg: float) -> np.ndarray:
    """ARKit world (x, y up, z) to local east, north, up, given the true azimuth of world -z."""
    th = math.radians(north_az_deg)
    e = p[..., 0] * math.cos(th) - p[..., 2] * math.sin(th)
    n = -p[..., 0] * math.sin(th) - p[..., 2] * math.cos(th)
    return np.stack([e, n, p[..., 1]], axis=-1)


def nwu_from_arkit_matrix(north_az_deg: float) -> np.ndarray:
    """Rotation from ARKit world to Core Motion xTrueNorthZVertical (x north, y west, z up)."""
    th = math.radians(north_az_deg)
    return np.array([[-math.sin(th), 0.0, -math.cos(th)], [-math.cos(th), 0.0, math.sin(th)], [0.0, 1.0, 0.0]])


def ar1(n: int, dt: float, tau: float, sigma: float, rng) -> np.ndarray:
    rho = math.exp(-dt / tau)
    x = np.empty(n)
    x[0] = rng.normal(0, sigma)
    w = rng.normal(0, sigma * math.sqrt(1 - rho * rho), n)
    for i in range(1, n):
        x[i] = rho * x[i - 1] + w[i]
    return x


def heading_error(ts: np.ndarray, rng) -> np.ndarray:
    """Compass error near a house (siding, wiring, the meter): bias + slow wander + jitter, deg."""
    dt = float(np.median(np.diff(ts))) if len(ts) > 1 else 0.01
    return rng.normal(0, 6.0) + ar1(len(ts), dt, 20.0, 5.0, rng) + rng.normal(0, 1.5, len(ts))


def motion_and_heading(traj: Trajectory, anchor: dict, north_az_deg: float, rng, rate_hz: float = 100.0):
    """CMDeviceMotion rows (xTrueNorthZVertical) and CLHeading rows, plus device gravity at any t."""
    ts = np.arange(traj.t0 - 1.0, traj.t1 + 1.0, 1.0 / rate_hz)
    Rc = traj.R(ts)
    Rwd = Rc @ DEVICE_FROM_CAMERA.T  # world_from_device
    herr = heading_error(ts, rng)
    nwu = nwu_from_arkit_matrix(north_az_deg)

    g_dev = np.einsum("nji,j->ni", Rwd, np.array([0.0, -1.0, 0.0])) + rng.normal(0, 0.002, (len(ts), 3))
    speed = np.linalg.norm(traj.vel(np.clip(ts, traj.t0, traj.t1)), axis=1)
    a_w = traj.acc(np.clip(ts, traj.t0, traj.t1)) / G
    moving = np.clip(speed / 0.5, 0, 1)
    phase = 2 * math.pi * 1.8 * ts + rng.uniform(0, 2 * math.pi)
    a_w[:, 1] += 0.10 * moving * np.sin(phase)
    a_w[:, 0] += 0.04 * moving * np.sin(phase / 2)
    a_dev = np.einsum("nji,nj->ni", Rwd, a_w) + rng.normal(0, 0.01, (len(ts), 3))

    rot = np.zeros((len(ts), 3))
    for i in range(1, len(ts)):
        rot[i] = log_so3(Rwd[i - 1].T @ Rwd[i]) * rate_hz
    rot[0] = rot[1] if len(ts) > 1 else 0
    rot += rng.normal(0, 0.01, rot.shape) + rng.normal(0, 0.002, 3)

    # Attitude in xTrueNorthZVertical, with the compass error as a yaw error about vertical.
    quats = np.empty((len(ts), 4))
    for i in range(len(ts)):
        Rz = Rotation.from_euler("z", -herr[i], degrees=True).as_matrix()
        quats[i] = rotmat_to_quat_xyzw(Rz @ nwu @ Rwd[i])

    # Earth's field in ARKit world: horizontal part toward magnetic north, vertical part down.
    inc = math.radians(anchor["inclination_deg"])
    mag_az = north_az_deg - anchor["declination_deg"]
    toward_mag_north = rot_y(-math.radians(mag_az)) @ np.array([0.0, 0.0, -1.0])
    B_w = anchor["field_uT"] * (math.cos(inc) * toward_mag_north + math.sin(inc) * np.array([0.0, -1.0, 0.0]))
    B_dev = np.einsum("nji,j->ni", Rwd, B_w) + ar1(len(ts), 1 / rate_hz, 10.0, 3.0, rng)[:, None] + rng.normal(0, 0.4, (len(ts), 3))

    view = -Rc[:, :, 2]  # camera -z in world
    true_heading = np.array([heading_deg_of_vector(v, north_az_deg) for v in view])
    true_heading_obs = (true_heading + herr) % 360
    cm_heading = true_heading_obs

    motion_rows = np.column_stack([ts, quats, rot, g_dev, a_dev, B_dev, np.full(len(ts), 2.0), cm_heading])

    acc_base = rng.uniform(10, 25)
    heading_rows = []
    last = None
    for i in range(0, len(ts), int(rate_hz / 10)):
        th = true_heading_obs[i]
        if last is not None and abs((th - last + 180) % 360 - 180) < 1.0:
            continue
        last = th
        heading_rows.append([
            ts[i], None, (th - anchor["declination_deg"]) % 360, th,
            max(5.0, acc_base + rng.normal(0, 2.0)), *B_dev[i],
        ])

    def gravity_at(t: float) -> list[float]:
        i = int(np.clip(round((t - ts[0]) * rate_hz), 0, len(ts) - 1))
        return [float(x) for x in g_dev[i]]

    return motion_rows, heading_rows, gravity_at, {"heading_error_deg_at": herr, "ts": ts}


def location_rows(traj: Trajectory, anchor: dict, north_az_deg: float, rng):
    """CLLocation updates at 1 Hz with a warm-up and correlated multipath error near buildings."""
    ts = np.arange(traj.t0 - 2.0, traj.t1 + 1.0, 1.0)
    p = traj.pos(np.clip(ts, traj.t0, traj.t1))
    v = traj.vel(np.clip(ts, traj.t0, traj.t1))
    enu = enu_from_arkit(p, north_az_deg)
    venu = enu_from_arkit(v, north_az_deg)
    n = len(ts)
    h_acc = rng.uniform(5.0, 12.0) * np.exp(rng.normal(0, 0.1, n))
    v_acc = rng.uniform(3.0, 10.0) * np.exp(rng.normal(0, 0.1, n))
    warm = np.array([65.0, 30.0, 16.0])
    k = min(3, n)
    h_acc[:k] = np.maximum(h_acc[:k], warm[:k])
    sig = h_acc / 1.5
    err_e = ar1(n, 1.0, 30.0, 1.0, rng) * sig
    err_n = ar1(n, 1.0, 30.0, 1.0, rng) * sig
    err_u = ar1(n, 1.0, 30.0, 1.0, rng) * v_acc / 1.5
    lat0, lon0 = anchor["latitude"], anchor["longitude"]
    rows = []
    for i in range(n):
        e, nn, u = enu[i] + np.array([err_e[i], err_n[i], err_u[i]])
        lat = lat0 + nn / 111320.0
        lon = lon0 + e / (111320.0 * math.cos(math.radians(lat0)))
        alt = anchor["altitude"] + u
        spd = float(np.hypot(venu[i, 0], venu[i, 1]))
        spd_obs = max(0.0, spd + rng.normal(0, 0.2))
        if spd_obs > 0.5:
            course = math.degrees(math.atan2(venu[i, 0], venu[i, 1])) % 360 + rng.normal(0, 8)
            course_acc = 15.0 + rng.uniform(0, 20)
        else:
            course, course_acc = -1.0, -1.0
        rows.append([ts[i], None, lat, lon, alt, alt + anchor["geoid_undulation_m"], float(h_acc[i]), float(v_acc[i]),
                     course % 360 if course >= 0 else -1.0, course_acc, spd_obs, float(rng.uniform(0.5, 1.5))])
    return rows


def feature_points(view, recon, lookup: dict, align, T_true, T_obs, scale: float, max_n: int, rng):
    """ARFrame.rawFeaturePoints for one keyframe: SfM points this photo observed, re-expressed in
    the observed (drifted) world, with depth noise. Identifiers are the SfM point ids, which, like
    ARKit's, persist across frames for the same feature."""
    if len(view.point_ids) == 0:
        return np.zeros((0, 3), np.float32), np.zeros(0, np.uint64)
    ids = np.unique(view.point_ids)
    if len(ids) > max_n:
        ids = rng.choice(ids, max_n, replace=False)
    X = align.point(recon.points[[lookup[int(i)] for i in ids]])
    Xc = (X - T_true[:3, 3]) @ T_true[:3, :3]
    depth = np.linalg.norm(Xc, axis=1)
    Xc = Xc * (1 + rng.normal(0, 0.02, len(Xc)))[:, None]
    Xo = scale * Xc @ T_obs[:3, :3].T + T_obs[:3, 3]
    keep = depth < 20.0
    return Xo[keep].astype(np.float32), ids[keep].astype(np.uint64)


def plane_anchors(points_ar: np.ndarray, cams: np.ndarray, T_true, T_obs, epochs, scales, profile: Profile,
                  classification_supported: bool, rng, max_planes: int = 8) -> list[dict]:
    """ARPlaneAnchor snapshots at session end from planes in the clean scene within 8 m of the
    walk. Vertical planes are found less often without LiDAR (PR #7: ARKit misses blank stucco)."""

    d = np.min(np.linalg.norm(points_ar[:, None, :] - cams[None, ::max(1, len(cams) // 40)], axis=2), axis=1)
    P = points_ar[d < 8.0]
    if len(P) > 60000:
        P = P[rng.choice(len(P), 60000, replace=False)]
    if len(P) < 200:
        return []
    planes = ransac_planes(P, threshold=0.04, max_planes=max_planes, min_frac=0.02, rng=rng)
    p_vertical = 0.95 if profile.lidar else (0.6 if classification_supported else 0.3)
    out = []
    for k, pl in enumerate(planes):
        n = pl.normal
        up = abs(n[1])
        if up > math.cos(math.radians(10)):
            align_kind = "horizontal"
            if n[1] < 0:
                n = -n
        elif up < math.sin(math.radians(10)):
            align_kind = "vertical"
            if rng.random() > p_vertical:
                continue
            centroid = P[pl.inliers].mean(0)
            near_cam = cams[np.argmin(np.linalg.norm(cams - centroid, axis=1))]
            if n @ (near_cam - centroid) < 0:
                n = -n
            n = np.array([n[0], 0.0, n[2]]) / np.hypot(n[0], n[2])
        else:
            continue
        X = P[pl.inliers]
        mu = X.mean(0)
        # Vertical anchors: x runs along the wall (width = length), z is vertical (height).
        a = np.array([1.0, 0, 0]) if align_kind == "horizontal" else np.cross([0.0, 1.0, 0.0], n)
        a = a - (a @ n) * n
        ax = a / np.linalg.norm(a)
        az = np.cross(ax, n)
        if align_kind == "horizontal":
            Y = (X - mu) @ np.stack([ax, az]).T
            _, _, Vt = np.linalg.svd(Y - Y.mean(0), full_matrices=False)
            ax, az = Vt[0] @ np.stack([ax, az]), np.cross(Vt[0] @ np.stack([ax, az]), n)
        uv = (X - mu) @ np.stack([ax, az]).T
        lo, hi = np.percentile(uv, 2, axis=0), np.percentile(uv, 98, axis=0)
        ctr2 = (lo + hi) / 2
        center = mu + ctr2[0] * ax + ctr2[1] * az
        uv = uv - ctr2
        inside = np.all((uv >= lo - ctr2) & (uv <= hi - ctr2), axis=1)
        try:
            hull = uv[inside][ConvexHull(uv[inside]).vertices]
        except Exception:
            hull = np.array([[lo[0], lo[1]], [hi[0], lo[1]], [hi[0], hi[1]], [lo[0], hi[1]]]) - ctr2
        # Anchor frame: y = normal, x = ax, z = x cross y; ARKit's plane lies in anchor x-z.
        A = np.eye(4)
        A[:3, 0], A[:3, 1], A[:3, 2], A[:3, 3] = ax, n, np.cross(ax, n), center
        kf = int(np.argmin(np.linalg.norm(cams - center, axis=1)))
        e = int(epochs[kf])
        M = np.eye(4)
        M[:3, :3] = T_obs[kf][:3, :3] @ T_true[kf][:3, :3].T
        M[:3, 3] = T_obs[kf][:3, 3] - scales[e] * (M[:3, :3] @ T_true[kf][:3, 3])
        A_obs = np.eye(4)
        A_obs[:3, :3] = M[:3, :3] @ A[:3, :3]
        A_obs[:3, 3] = scales[e] * (M[:3, :3] @ center) + M[:3, 3]
        size = (hi - lo) * scales[e]
        if align_kind == "horizontal":
            cls = "floor" if center[1] < np.median(cams[:, 1]) - 0.5 else "none"
        else:
            cls = "wall"
        out.append({
            "id": f"plane-{k + 1:02d}",
            "epoch": f"e{e + 1}",
            "alignment": align_kind,
            "classification": cls if classification_supported else "notAvailable",
            "transform": A_obs,
            "center": [0.0, 0.0, 0.0],
            "planeExtent": {"width": float(size[0]), "height": float(size[1]), "rotationOnYAxis": 0.0},
            "boundary": (hull * scales[e]).tolist(),
            "sourceInliers": int(len(X)),
        })
    return out
