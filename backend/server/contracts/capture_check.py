"""Closed capture-check and hint codes. WS-14 maps these to customer copy."""

from __future__ import annotations

from typing import Literal

CaptureCheckCode = Literal[
    "meter_close",
    "meter_oblique",
    "meter_closeups_in_session",
    "meter_tap",
    "door_view_meter_side",
    "panel_close",
    "panel_open",
    "main_disconnect",
    "keyframe_count",
    "keyframe_cadence",
    "tracking_quality",
    "photo_count",
    "wall_left_of_meter",
    "wall_right_of_meter",
    "ground_downward_left",
    "ground_downward_right",
]

# The 16 capture-check codes, plus WS-03 frame hints, plus WS-08 read hints.
HintCode = Literal[
    "meter_close",
    "meter_oblique",
    "meter_closeups_in_session",
    "meter_tap",
    "door_view_meter_side",
    "panel_close",
    "panel_open",
    "main_disconnect",
    "keyframe_count",
    "keyframe_cadence",
    "tracking_quality",
    "photo_count",
    "wall_left_of_meter",
    "wall_right_of_meter",
    "ground_downward_left",
    "ground_downward_right",
    "frames_blurry",
    "frames_dark",
    "frames_overexposed",
    "wall_gap_left",
    "wall_gap_right",
    "ground_not_seen_left",
    "ground_not_seen_right",
    "meter_plate_unreadable",
    "meter_number_confirm",
    "panel_label_unreadable",
]
