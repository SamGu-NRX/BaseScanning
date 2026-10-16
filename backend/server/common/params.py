"""Scene and coverage parameters.

Scene-stage knobs are ``HSS_SCENE_*``. Grid and request knobs are ``HSS_COVERAGE_*``.
``GROUND_RULE_DEFAULT`` stays the bare shared knob (SD-11).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass


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
class SceneParams:
    voxel_m: float = 0.05
    relief_m: float = 0.10
    relief_rel: float = 0.04
    ground_rule: str = "angle80"
    detector_pm_extra_m: float = 0.20
    fallback_floor_m: float = 0.15
    corner_refit_radius_m: float = 3.0
    glass_behind_m: tuple[float, float] = (0.2, 1.2)
    complete_walls: bool = True
    plane_min_extent_m: float = 1.0
    completion_pm_m: float = 0.30
    phantom_min_sparse_pts: int = 30
    # Step 10 (g), names from the brief. [default chosen]
    ground_guard_max_above_base_m: float = 0.30
    ground_base_min_pts: int = 60
    ground_base_min_views: int = 2
    ground_unobserved_pm_m: float = 0.30
    views_needed_max: int = 8
    seed: int = 7
    timeout_dense_s: float = 280.0
    timeout_coverage_s: float = 110.0
    timeout_scene_s: float = 110.0

    def as_dict(self) -> dict:
        raw = asdict(self)
        raw["glass_behind_m"] = list(self.glass_behind_m)
        return raw


def scene_params_from_env() -> SceneParams:
    rule = os.environ.get("GROUND_RULE_DEFAULT", "angle80")
    return SceneParams(
        voxel_m=_env_float("HSS_COVERAGE_VOXEL_M", 0.05),
        relief_m=_env_float("HSS_COVERAGE_RELIEF_M", 0.10),
        relief_rel=_env_float("HSS_COVERAGE_RELIEF_REL", 0.04),
        ground_rule=os.environ.get("HSS_SCENE_GROUND_RULE", rule),
        detector_pm_extra_m=_env_float("HSS_SCENE_DETECTOR_PM_EXTRA_M", 0.20),
        fallback_floor_m=_env_float("HSS_SCENE_FALLBACK_FLOOR_M", 0.15),
        corner_refit_radius_m=_env_float("HSS_SCENE_CORNER_REFIT_RADIUS_M", 3.0),
        complete_walls=_env_bool("HSS_SCENE_COMPLETE_WALLS", True),
        plane_min_extent_m=_env_float("HSS_SCENE_PLANE_MIN_EXTENT_M", 1.0),
        completion_pm_m=_env_float("HSS_SCENE_COMPLETION_PM_M", 0.30),
        phantom_min_sparse_pts=_env_int("HSS_SCENE_PHANTOM_MIN_SPARSE_PTS", 30),
        ground_guard_max_above_base_m=_env_float("HSS_SCENE_GROUND_GUARD_MAX_ABOVE_BASE_M", 0.30),
        ground_base_min_pts=_env_int("HSS_SCENE_GROUND_BASE_MIN_PTS", 60),
        ground_base_min_views=_env_int("HSS_SCENE_GROUND_BASE_MIN_VIEWS", 2),
        ground_unobserved_pm_m=_env_float("HSS_SCENE_GROUND_UNOBSERVED_PM_M", 0.30),
        views_needed_max=_env_int("HSS_COVERAGE_VIEWS_NEEDED_MAX", 8),
        seed=_env_int("HSS_SCENE_SEED", 7),
        timeout_dense_s=_env_float("HSS_DENSE_TIMEOUT_S", 280.0),
        timeout_coverage_s=_env_float("HSS_COVERAGE_TIMEOUT_S", 110.0),
        timeout_scene_s=_env_float("HSS_SCENE_TIMEOUT_S", 110.0),
    )
