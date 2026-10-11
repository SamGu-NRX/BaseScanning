"""Observations: the raw pixel facts a rig of keyframes exports about a world.

An observation is exactly what the no-LiDAR export carries about the wall and
nothing more: for each landmark and each keyframe, the landmark's pixel in that
keyframe's unrotated image, or null when no keyframe observes it. The keyframes
themselves (pose, intrinsics, w, h) are part of the export too, and are the same
across worlds — the walk is what it is; the world only decides what the cameras
see. Landmark identity across keyframes is a declared capability assumption
(README.md, A2), backed by the feature-point identifiers the contract already
carries (packet/README.md, "Already in 0.4 ... feature-point identifiers").

Hashing: the observation map is canonicalized (sorted keys, pixels rounded to
QUANTIZE_PX decimals) and hashed with sha256. Two worlds are exactly equivalent
when their hashes match — equality at 0.001 px, the study's declared bar. No
noise, occlusion, or correspondence error enters the model; those are measured-
phone questions, out of scope here (README.md, assumptions).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np

from .projection import Camera
from .worlds import WallWorld, landmarks

QUANTIZE_PX = 3  # decimals kept in the observation map and its hash

Observation = dict[str, dict[str, list[float] | None]]  # landmark -> keyframe -> [u, v] | None


def observe(world: WallWorld, cameras: list[Camera]) -> Observation:
    """What the rig observes of the world: every landmark's pixel per keyframe."""
    pts = landmarks(world)
    keys = list(pts)
    stacked = np.stack([pts[k] for k in keys])
    obs: Observation = {k: {} for k in keys}
    for cam in cameras:
        seen = cam.project(stacked)
        for i, key in enumerate(keys):
            pixel = seen.get((i, 0))
            obs[key][cam.keyframe_id] = (
                [round(pixel[0], QUANTIZE_PX), round(pixel[1], QUANTIZE_PX)]
                if pixel is not None
                else None
            )
    return obs


def observation_hash(obs: Observation) -> str:
    """sha256 over the canonical observation map (sorted keys, rounded pixels)."""
    text = json.dumps(obs, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()


def visible_landmarks(obs: Observation) -> dict[str, list[str]]:
    """Per landmark, the keyframes that observe it (null-free pixels)."""
    return {
        key: [kf for kf, pixel in views.items() if pixel is not None] for key, views in obs.items()
    }


def as_json(obs: Observation) -> dict[str, Any]:
    """The observation map as JSON-safe data (already is; kept for typing)."""
    return obs  # type: ignore[return-value]
