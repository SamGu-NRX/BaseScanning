"""ETH3D's true camera poses degraded to what a phone's AR tracking would report.

Each setting is an assumption, stated with where its numbers come from:

- `exact`: the true poses, the best any pose prior can do.
- `advio_2018`: the errors measured in `evals.drift` on ADVIO's 2018 iPhone 6s. Scale: each
  seed takes one walk's ARKit scale against the ground truth rescaled to GPS, by seed identity
  (`arkit_scale_vs_truth_gps` in results/advio_drift.json, walks 20 to 22: 0.838, 0.943, 0.951;
  the median displacement ratio over 10 to 30 ft windows). ARCore is not the reference: on walk
  20 it reads 4% short of GPS itself (0.802 vs 0.835 of the truth). Position noise: ARKit's own
  spread over 3 ft is about 3 in (three-cornered hat, walks 20 to 22), which is two independent
  position errors, so 5 cm per axis per camera. Rotation noise: 0.2 degrees per axis, the median
  disagreement between ARKit and image-derived rotations measured while building the replay
  session (an upper bound: it includes the image estimate's own error).
- `modern_assumed`: a guess for a current iPhone, with no measurement behind it: 2% short, 1 cm
  and 0.1 degrees.

Scale error stretches every camera's offset from the group's first camera; noise is then added
independently per camera. Rotation noise turns each camera about its own centre. Each setting with
noise is drawn `NOISE_DRAWS` times (draw 0 is used for new MapAnything runs); the scale a group
gets does not change between draws.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PoseError:
    scales: tuple[float, ...]  # selected by seed identity, shared across view counts
    position_sigma_m: float
    rotation_sigma_deg: float
    # How far a correct match may reproject with these poses: 2 px of feature noise plus what the
    # pose noise moves a point 5 m away at the 1024 px focal length (about 560 px): 0.2 degrees is
    # 2 px, 5 cm is 6 px. A tighter threshold discards consistent matches and the photo's scale.
    reprojection_px: float


# ARKit / GPS-rescaled truth per ADVIO walk (20, 21, 22), from results/advio_drift.json;
# tests/test_pose_priors.py fails if that file changes.
ADVIO_ARKIT_SCALES = (0.8377, 0.9425, 0.9509)

SETTINGS = {
    "exact": PoseError((1.0,), 0.0, 0.0, 2.0),
    "advio_2018": PoseError(ADVIO_ARKIT_SCALES, 0.05, 0.2, 8.0),
    "modern_assumed": PoseError((0.98,), 0.01, 0.1, 3.0),
}
NOISE_DRAWS = 5


def noise_draws(setting: str) -> range:
    """Draw indices for a setting: one for noise-free poses (every draw would be identical)."""
    error = SETTINGS[setting]
    noisy = error.position_sigma_m > 0 or error.rotation_sigma_deg > 0
    return range(NOISE_DRAWS if noisy else 1)


def small_rotation(axis_angle_rad: np.ndarray) -> np.ndarray:
    """Rotation matrix for a rotation vector (Rodrigues)."""
    theta = float(np.linalg.norm(axis_angle_rad))
    if theta == 0:
        return np.eye(3)
    k = axis_angle_rad / theta
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(theta) * K + (1 - np.cos(theta)) * K @ K


def degrade(
    cam_to_world: list[np.ndarray], scale: float, error: PoseError, rng: np.random.Generator
) -> list[np.ndarray]:
    """AR-like poses for one group: offsets from the first camera scaled, then noise added."""
    origin = cam_to_world[0][:3, 3]
    out = []
    for T in cam_to_world:
        D = np.eye(4)
        D[:3, 3] = origin + scale * (T[:3, 3] - origin)
        D[:3, 3] += rng.normal(0.0, error.position_sigma_m, 3)
        noise = small_rotation(np.radians(rng.normal(0.0, error.rotation_sigma_deg, 3)))
        D[:3, :3] = T[:3, :3] @ noise
        out.append(D)
    return out


def group_poses(
    views: dict, groups: dict[str, list[list[str]]], setting: str, draw: int = 0
) -> dict[str, dict]:
    """Pose noise keyed by setting, draw, seed and camera, shared by nested view groups."""
    error = SETTINGS[setting]
    out: dict[str, dict] = {}
    for n, members_list in groups.items():
        for members in members_list:
            seed = members[0]
            scale = error.scales[_seed(seed) % len(error.scales)]
            poses = {}
            for member in members:
                rng = np.random.default_rng([_seed(setting), draw, _seed(seed), _seed(member)])
                T = views[member].cam_to_world.copy()
                origin = views[seed].cam_to_world[:3, 3]
                T[:3, 3] = origin + scale * (T[:3, 3] - origin)
                poses[member] = degrade([T], 1.0, error, rng)[0].tolist()
            out[f"n{n}-{seed}"] = {"scale": scale, "poses": poses}
    return out


def _seed(setting: str) -> int:
    """A fixed seed per setting (Python's hash() is salted per process, so it is not used)."""
    return sum(ord(c) * 31**i for i, c in enumerate(setting)) % 2**32
