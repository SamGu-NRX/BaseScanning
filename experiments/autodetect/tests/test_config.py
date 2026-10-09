"""Contract tests for autodetect.config.

These pin the numbers the pass criteria and the per-model settings depend on. A failure
means a constant drifted from the value README.md and the first scored run agreed on, so
every published score needs a rerun before it means anything.
"""

from autodetect.config import (
    EXTENT_P90_FT,
    GDINO_PHRASES,
    IOU,
    KEYFRAME_MS,
    LIVE_MS,
    MAX_DETS,
    MIN_PRECISION,
    MIN_RECALL,
    MIN_SCORE,
    NEAR_MIN_SIDE,
    NMS_IOU,
    OWL_QUERIES,
    STUDENT_CONFIDENCE_FLOOR,
    STUDENT_NMS_IOU,
    VISION_RECTS,
)


def test_pass_bars_match_the_readme_criteria():
    assert MIN_RECALL == 0.80
    assert MIN_PRECISION == 0.60
    assert IOU == 0.5


def test_prompt_tables_list_the_same_objects_in_the_same_order():
    # owl.py and gdino.py both turn dict order into class indices, so the two tables must
    # agree on the key sequence, not just the key set.
    assert list(OWL_QUERIES) == list(GDINO_PHRASES) == [
        "window",
        "door",
        "garage_door",
        "ac",
        "gas_meter",
        "elec_box",
        "battery",
    ]


def test_open_vocabulary_postprocess_settings():
    assert NMS_IOU == 0.5
    assert MIN_SCORE == 0.01
    assert MAX_DETS == 100


def test_speed_class_boundaries():
    assert LIVE_MS == 100
    assert KEYFRAME_MS == 1000
    assert LIVE_MS < KEYFRAME_MS


def test_near_sized_cut_sits_below_the_motivating_15_percent():
    assert NEAR_MIN_SIDE == 0.10
    assert NEAR_MIN_SIDE < 0.15


def test_extent_bar():
    assert EXTENT_P90_FT == 0.5


def test_student_settings():
    assert STUDENT_CONFIDENCE_FLOOR == 0.01
    assert STUDENT_NMS_IOU == 0.45


def test_vision_rectangle_request_settings():
    assert VISION_RECTS == {
        "maximumObservations": 0,
        "minimumAspectRatio": 0.2,
        "maximumAspectRatio": 1.0,
        "minimumSize": 0.03,
        "quadratureTolerance": 30.0,
        "minimumConfidence": 0.0,
    }
