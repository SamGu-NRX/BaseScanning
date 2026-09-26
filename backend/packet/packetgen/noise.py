"""ARKit-like pose error, applied to clean gravity-aligned ARKit poses.

The model extends PR #12's `evals/ar_poses.py` (one scale per walk plus independent per-camera
noise) with drift, because PR #12's own drift tables grow with distance walked:

- scale: one multiplicative factor per coordinate epoch on every displacement (VIO scale error);
- yaw drift: a random walk in heading about world +y, per metre of horizontal path. Pitch and roll
  are observable from gravity, so they get jitter only;
- position drift: a random walk per metre of path (horizontal and vertical);
- jitter: independent per-frame position and rotation noise;
- tracking resets: with some probability per session the tracker loses the map, and every later
  frame lives in a new world (new epoch: new origin, new arbitrary heading), as after
  ARSession.run(_:options: [.resetTracking]) or a failed relocalization.

Profiles live in profiles.json, fitted by `packetgen.calibrate` against PR #12's measurements.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .conventions import rodrigues, rot_y

PROFILES_PATH = Path(__file__).with_name("profiles.json")


@dataclass
class ScaleComponent:
    weight: float
    mean: float  # relative error, e.g. -0.02 is 2% short
    sd: float


@dataclass
class Profile:
    name: str
    description: str
    lidar: bool
    scale: list[ScaleComponent]
    scale_clip: tuple[float, float]
    jitter_pos_m: float
    jitter_rot_deg: float
    rw_pos_h_m_per_sqrt_m: float
    rw_pos_v_m_per_sqrt_m: float
    rw_yaw_deg_per_sqrt_m: float
    reset_probability: float
    init_seconds: float
    limited_inflation: float
    focal_sd_rel: float
    principal_sd_px: float
    excessive_motion_deg_per_s: float
    insufficient_features_min: int
    session_sigma_lognormal: float = 0.0
    evidence: dict = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict) -> "Profile":
        d = dict(d)
        d["scale"] = [ScaleComponent(**c) for c in d["scale"]]
        d["scale_clip"] = tuple(d["scale_clip"])
        return Profile(**d)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["scale"] = [c.__dict__ for c in self.scale]
        d["scale_clip"] = list(self.scale_clip)
        return d

    def draw_scale(self, rng: np.random.Generator) -> float:
        w = np.array([c.weight for c in self.scale])
        k = rng.choice(len(self.scale), p=w / w.sum())
        e = rng.normal(self.scale[k].mean, self.scale[k].sd)
        return float(1.0 + np.clip(e, *self.scale_clip))


def load_profiles(path: Path = PROFILES_PATH) -> dict[str, Profile]:
    data = json.loads(path.read_text())
    return {name: Profile.from_dict(p) for name, p in data["profiles"].items()}


def horizontal_path_length(p: np.ndarray) -> np.ndarray:
    """Cumulative horizontal (x, z) path length of y-up positions, starting at 0."""
    step = np.linalg.norm(np.diff(p[:, [0, 2]], axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(step)])


@dataclass
class DriftDraw:
    """The random quantities of one session, kept for the truth sidecar."""

    scales: list[float]
    session_multiplier: float
    reset_index: int | None
    reset_heading_deg: float | None
    yaw_error_deg: np.ndarray
    drift_m: np.ndarray
    jitter_pos_m: np.ndarray
    jitter_rot_rad: np.ndarray


def apply_drift(
    R: np.ndarray,
    c: np.ndarray,
    profile: Profile,
    rng: np.random.Generator,
    limited: np.ndarray | None = None,
    reset_index: int | None = -1,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, DriftDraw]:
    """Corrupt clean poses (R: (N,3,3) camera-to-world rotations, c: (N,3) centres, y up, in
    capture order) with one draw of `profile`.

    Returns observed rotations, observed centres, epoch index per frame, and the draw.
    `reset_index=-1` lets the profile decide; None forbids a reset; an int forces one there.
    Frame 0 of each epoch sits at that epoch's origin, as ARKit's first frames do."""
    n = len(c)
    limited = np.zeros(n, bool) if limited is None else limited
    if reset_index == -1:
        reset_index = None
        if n >= 6 and rng.random() < profile.reset_probability:
            reset_index = int(rng.integers(max(2, n // 5), n - 2))
    s = horizontal_path_length(c)
    ds = np.diff(s, prepend=0.0)
    # Sessions differ (light, texture, how the phone is moved): one log-normal multiplier on
    # every drift and jitter term gives the heavy walk-to-walk tail PR #12 measured.
    m = float(np.exp(rng.normal(0.0, profile.session_sigma_lognormal)))

    yaw = np.cumsum(rng.normal(0.0, 1.0, n) * m * np.radians(profile.rw_yaw_deg_per_sqrt_m) * np.sqrt(ds))
    drift = np.cumsum(
        rng.normal(0.0, 1.0, (n, 3))
        * np.sqrt(ds)[:, None]
        * m
        * np.array([profile.rw_pos_h_m_per_sqrt_m, profile.rw_pos_v_m_per_sqrt_m, profile.rw_pos_h_m_per_sqrt_m]),
        axis=0,
    )
    infl = m * np.where(limited, profile.limited_inflation, 1.0)
    jit_p = rng.normal(0.0, profile.jitter_pos_m, (n, 3)) * infl[:, None]
    jit_r = rng.normal(0.0, np.radians(profile.jitter_rot_deg), (n, 3)) * infl[:, None]

    epoch = np.zeros(n, int)
    scales = [profile.draw_scale(rng)]
    reset_heading = None
    if reset_index is not None:
        epoch[reset_index:] = 1
        scales.append(profile.draw_scale(rng))
        reset_heading = float(rng.uniform(-180, 180))

    R_obs = np.empty_like(R)
    c_obs = np.empty_like(c)
    for e in np.unique(epoch):
        idx = np.flatnonzero(epoch == e)
        i0 = idx[0]
        k = scales[e]
        # Each epoch restarts its own world: origin at its first frame, heading from that frame's
        # facing (epoch 0 keeps the caller's world; later epochs get a random extra heading).
        extra = rot_y(np.radians(reset_heading)) if e > 0 else np.eye(3)
        yaw0, drift0 = yaw[i0], drift[i0]
        prev_true = c[i0]
        acc = np.zeros(3) if e > 0 else c[i0].copy()
        for i in idx:
            step = c[i] - prev_true
            prev_true = c[i]
            acc = acc + k * (rot_y(yaw[i] - yaw0) @ step)
            pos = acc + (drift[i] - drift0)
            if e > 0:
                pos = extra @ pos
            c_obs[i] = pos + jit_p[i]
            Rw = rot_y(yaw[i] - yaw0)
            if e > 0:
                Rw = extra @ Rw
            R_obs[i] = Rw @ R[i] @ rodrigues(jit_r[i])
    draw = DriftDraw(
        scales=scales,
        session_multiplier=m,
        reset_index=reset_index,
        reset_heading_deg=reset_heading,
        yaw_error_deg=np.degrees(yaw),
        drift_m=drift,
        jitter_pos_m=jit_p,
        jitter_rot_rad=jit_r,
    )
    return R_obs, c_obs, epoch, draw


def corrupt_intrinsics(K: tuple[float, float, float, float], width: int, profile: Profile, rng: np.random.Generator):
    """ARKit reports factory intrinsics per video format, not a per-scene calibration; model a
    small per-session focal and principal-point error (scaled from 1920 px)."""
    fx, fy, cx, cy = K
    f = 1.0 + rng.normal(0.0, profile.focal_sd_rel)
    px = profile.principal_sd_px * width / 1920.0
    return (fx * f, fy * f, cx + rng.normal(0.0, px), cy + rng.normal(0.0, px))
