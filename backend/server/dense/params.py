"""Dense-stage parameters. Env knobs are ``HSS_DENSE_*`` (SD-11)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DenseParams:
    window: int = 8
    stride: int = 4
    same_facing_cos: float = 0.5
    conf_threshold: float = 0.15
    flying_pixel_rel: float = 0.05
    window_rms_max_m: float = 0.10
    window_scale_dev_max: float = 0.05
    second_opinion: str = "auto"
    disagree_max_m: float = 0.15
    lidar_method: str = "tsdf"
    geometry: str = "auto"
    lidar_max_m: float = 5.0
    lidar_min_m: float = 0.2
    lidar_min_conf: int = 1
    tsdf_voxel_m: float = 0.04
    tsdf_trunc_vox: float = 4.0
    tsdf_min_weight: float = 1.0
    depth_model: str = "moge2-vits"
    weights_dir: Path | None = None
    max_depth_frames: int = 100
    dense_cap: int = 500_000

    def resolved_weights(self) -> Path:
        if self.weights_dir is not None:
            return self.weights_dir
        return Path(os.environ.get("HSS_DENSE_WEIGHTS", "/vol/hf"))


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else float(raw)


def dense_params_from_env() -> DenseParams:
    weights = os.environ.get("HSS_DENSE_WEIGHTS")
    return DenseParams(
        window=_env_int("HSS_DENSE_WINDOW", 8),
        stride=_env_int("HSS_DENSE_STRIDE", 4),
        lidar_method=os.environ.get("HSS_DENSE_LIDAR_METHOD", "tsdf"),
        geometry=os.environ.get("HSS_DENSE_GEOMETRY", "auto"),
        weights_dir=Path(weights) if weights else None,
        second_opinion=os.environ.get("HSS_DENSE_SECOND_OPINION", "auto"),
        max_depth_frames=_env_int("HSS_DENSE_MAX_DEPTH_FRAMES", 100),
        dense_cap=_env_int("HSS_DENSE_CAP", 500_000),
        disagree_max_m=_env_float("HSS_DENSE_DISAGREE_MAX_M", 0.15),
    )
