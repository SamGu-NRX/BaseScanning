"""Scale-stage knobs. Per-area names are ``HSS_SCALE_*``; ``VISION_EXTERNAL_CALLS`` is shared."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ScaleParams:
    lidar_max_m: float = 5.0
    lidar_min_m: float = 0.2
    lidar_min_pairs: int = 50
    lidar_verified_pairs: int = 200
    lidar_sd_floor: float = 0.005
    arkit_band_pct: float = 10.0
    arkit_band_pct_2018: float = 30.0
    identity_min_confidence: float = 0.5
    allow_assumed_dimensions: bool = False
    meter_verified_enabled: bool = False
    truedepth_fuse: bool = True
    footprint_check: bool = False
    external_vision: bool = True
    table_path: Path = Path("data/meter-models/meter_models.v1.json")
    seed: int = 0

    @classmethod
    def from_env(cls) -> ScaleParams:
        vision = os.environ.get("VISION_EXTERNAL_CALLS", "on").strip().lower()
        table = os.environ.get("HSS_SCALE_METER_MODELS_PATH")
        return cls(
            lidar_max_m=_float("HSS_SCALE_LIDAR_MAX_M", 5.0),
            lidar_min_m=_float("HSS_SCALE_LIDAR_MIN_M", 0.2),
            lidar_min_pairs=int(_float("HSS_SCALE_LIDAR_MIN_PAIRS", 50)),
            lidar_verified_pairs=int(_float("HSS_SCALE_LIDAR_VERIFIED_PAIRS", 200)),
            lidar_sd_floor=_float("HSS_SCALE_LIDAR_SD_FLOOR", 0.005),
            arkit_band_pct=_float("HSS_SCALE_ARKIT_BAND_PCT", 10.0),
            arkit_band_pct_2018=_float("HSS_SCALE_ARKIT_BAND_PCT_2018", 30.0),
            identity_min_confidence=_float("HSS_SCALE_IDENTITY_MIN_CONFIDENCE", 0.5),
            allow_assumed_dimensions=_flag("HSS_SCALE_ALLOW_ASSUMED_DIMENSIONS", False),
            meter_verified_enabled=_flag("HSS_SCALE_METER_VERIFIED_ENABLED", False),
            truedepth_fuse=_flag("HSS_SCALE_TRUEDEPTH_FUSE", True),
            footprint_check=_flag("HSS_SCALE_FOOTPRINT_CHECK", False),
            external_vision=vision != "off",
            table_path=Path(table) if table else Path("data/meter-models/meter_models.v1.json"),
            seed=int(_float("HSS_SCALE_SEED", 0)),
        )
