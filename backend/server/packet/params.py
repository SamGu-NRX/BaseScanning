"""Thresholds for packet validation. No environment overrides."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PacketParams:
    """Every numeric gate lives here. The brief's final pass already folds in A7 and A10."""

    rotation_error: float = 1e-3
    rotation_warn: float = 1e-4
    translation_absurd_m: float = 1000.0
    fov_error_deg: tuple[float, float] = (20.0, 150.0)
    fov_warn_deg: tuple[float, float] = (45.0, 100.0)
    focal_aspect: float = 0.02
    principal_off_frac: float = 0.05
    intrinsics_rescale_frac: float = 0.20
    low_resolution_px: int = 640
    timestamp_dup_s: float = 1e-3
    timestamp_dup_m: float = 0.01
    timestamp_before_epoch_s: float = 0.5
    pose_jump_m_s: float = 3.0
    pose_jump_min_dt_s: float = 0.1
    pose_spin_deg_s: float = 180.0
    tracking_unstable_share: float = 0.05
    limited_tracking_share: float = 0.30
    gravity_warn_deg: float = 3.0
    gravity_error_deg: float = 15.0
    gravity_error_min_rows: int = 100
    gravity_match_s: float = 0.05
    gravity_mag: tuple[float, float] = (0.9, 1.1)
    pitch_up_deg: float = 30.0
    pitch_down_deg: float = -45.0
    depth_finite_share: float = 0.5
    depth_median_m: tuple[float, float] = (0.2, 10.0)
    depth_sample_stride: int = 10
    images_usable_share: float = 0.60
    images_usable_min: int = 10
    heic_quality: int = 92
    focal_assumed_mm: float = 26.0
    focal_rel_bound_read: float = 0.02
    focal_rel_bound_assumed: float = 0.15
    intrinsics_recompute_frac: float = 0.01
    f35_wide_mm: tuple[float, float] = (22.0, 30.0)
    f35_range_mm: tuple[float, float] = (10.0, 200.0)
    tap_replay: float = 1e-3
    tap_hit_m: tuple[float, float] = (0.2, 30.0)
    close_up_m: tuple[float, float] = (0.3, 3.0)
    head_on_max_deg: float = 30.0
    oblique_deg: tuple[float, float] = (15.0, 50.0)
    stream_rate_frac: float = 0.5
    imu_g: tuple[float, float] = (0.9, 1.1)
    imu_ms2: tuple[float, float] = (9.0, 10.6)
    standard_g: float = 9.80665
    arkit_pose_pos_m: float = 0.05
    slow_exposure_s: float = 1.0 / 60.0
    # Brief §4 step 15: flag when ISO is above 200, not at 1600.
    iso_high: float = 200.0
    brightness_dark: float = 3.0
    # exposure × |gyro rad/s| × fx, in pixels. Flag when this is above 4.
    blur_px_max: float = 4.0
    blurred_share_max: float = 0.30
    few_images: int = 10
    keyframe_count_min: int = 40
    # Brief §4 step 16 keeps 0.6 m. Denser 0.5 m spacing is WS-04's photo_spacing check.
    median_spacing_m: float = 0.6
    keyframes_per_s_min: float = 1.5
    photo_count_min: int = 40
    format_version: str = "0.4"


DEFAULT_PARAMS = PacketParams()
