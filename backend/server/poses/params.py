"""Mapper knobs. Defaults are the A6 ``prior_s03`` recipe plus the SD-10 budget."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PoseParams:
    prior_sigma_m: float = 0.03
    robust_prior: bool = False
    sfm_max_frames: int = 48
    max_image_size: int = 2048
    max_features: int = 8192
    spatial_neighbors: int = 40
    spatial_radius_m: float = 20.0
    sequential_overlap: int = 10
    exhaustive_max_images: int = 200
    min_epoch_frames: int = 3
    fallback_min_registered: float = 0.90
    # A7 §6.6: |SfM-vs-ARKit scale percent| above this falls back.
    fallback_max_scale_dev_pct: float = 5.0
    scale_warn_pct: float = 3.0
    bent_rms_m: float = 0.25
    # SD-88 / diagnosis §4. Swap to fixed-ARKit when the prior model
    # (mapper + post-registered, not ARKit-carried frames) is under this
    # share. The logged 90% split stays ``fallback_min_registered``.
    select_min_registered: float = 0.60
    closeup_min_points3d: int = 30
    closeup_max_reproj_px: float = 2.0
    register_max_reproj_px: float = 2.0
    register_min_points3d: int = 30
    seed: int = 0
    gpu: bool = True
    # RANSAC and mapper seeds make 4 threads match 1 thread. One thread costs
    # about 3.4× in matching and is not required for a byte-identical pair.
    num_threads: int = 4
    # A7: exposure × |gyro| × focal above this many pixels is dropped before matching.
    blur_max_px: float = 4.0
    # Used only when the packet has no exposure/gyro. Tuned on the synthetic wall.
    blur_laplacian_min: float = 15.0
    # A10: photos about 0.9 m apart along a long walk fail to register.
    spacing_gap_m: float = 0.9
    spacing_path_m: float = 20.0
    sparse_photo_min_registered: int = 20
    timeout_s: float = 400.0
    keep_db: bool = False

    @classmethod
    def from_env(cls) -> PoseParams:
        return cls(
            prior_sigma_m=_env_float("HSS_POSES_PRIOR_SIGMA_M", 0.03),
            robust_prior=_env_bool("HSS_POSES_ROBUST_PRIOR", False),
            sfm_max_frames=_env_int("HSS_POSES_SFM_MAX_FRAMES", 48),
            max_image_size=_env_int("HSS_POSES_MAX_IMAGE_SIZE", 2048),
            spatial_neighbors=_env_int("HSS_POSES_SPATIAL_NEIGHBORS", 40),
            spatial_radius_m=_env_float("HSS_POSES_SPATIAL_RADIUS_M", 20.0),
            sequential_overlap=_env_int("HSS_POSES_SEQUENTIAL_OVERLAP", 10),
            seed=_env_int("HSS_POSES_SEED", 0),
            gpu=_env_bool("HSS_POSES_GPU", True),
            num_threads=_env_int("HSS_POSES_THREADS", 4),
            blur_max_px=_env_float("HSS_POSES_BLUR_MAX_PX", 4.0),
            fallback_max_scale_dev_pct=_env_float("HSS_POSES_SCALE_REJECT_PCT", 5.0),
            timeout_s=_env_float("HSS_POSES_TIMEOUT_S", 400.0),
            keep_db=_env_bool("HSS_POSES_KEEP_DB", False),
        )
