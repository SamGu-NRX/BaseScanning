import math

import numpy as np
import pytest

from autodetect.oieval import (
    MERGED,
    ClassEntries,
    at_threshold,
    average_precision,
    choose_threshold,
    class_entries,
    image_entries,
)

A = [0.1, 0.1, 0.3, 0.3]
B = [0.6, 0.6, 0.9, 0.9]
GROUP = [0.0, 0.5, 1.0, 1.0]


def det(box, score, label="window"):
    return {"label": label, "score": score, "box": box}


def gt(box, label="window", group=False):
    return {"label": label, "box": box, "group": group}


def test_duplicate_detection_of_a_matched_box_is_a_false_positive():
    scores, tp, n = image_entries([gt(A)], [det(A, 0.9), det(A, 0.8)])
    assert n == 1
    assert scores == [0.9, 0.8]
    assert tp == [True, False]


def test_iou_below_half_is_a_false_positive():
    shifted = [0.2, 0.1, 0.4, 0.3]  # IoU with A is 1/3
    _, tp, _ = image_entries([gt(A)], [det(shifted, 0.9)])
    assert tp == [False]


def test_group_counts_one_true_positive_at_its_best_score_and_absorbs_the_rest():
    inside = [[0.1, 0.6, 0.2, 0.7], [0.3, 0.6, 0.4, 0.7], [0.5, 0.6, 0.6, 0.7]]
    dets = [det(b, s) for b, s in zip(inside, (0.4, 0.7, 0.5))]
    scores, tp, n = image_entries([gt(GROUP, group=True)], dets)
    assert n == 1
    assert scores == [0.7]
    assert tp == [True]


def test_group_match_uses_the_detections_own_area():
    # Detection much larger than the group box: IoA over the detection is small, so it is a FP.
    big = [0.0, 0.0, 1.0, 1.0]
    small_group = [0.4, 0.4, 0.5, 0.5]
    scores, tp, n = image_entries([gt(small_group, group=True)], [det(big, 0.9)])
    assert (scores, tp, n) == ([0.9], [False], 1)


def test_single_box_matches_before_group_and_duplicate_inside_group_is_absorbed():
    single = [0.1, 0.6, 0.3, 0.8]
    dets = [det(single, 0.9), det(single, 0.8)]
    scores, tp, n = image_entries([gt(single), gt(GROUP, group=True)], dets)
    assert n == 2
    # first matches the single box; the duplicate falls inside the group and becomes its TP
    assert sorted(zip(scores, tp)) == [(0.8, True), (0.9, True)]


def test_undetected_group_is_one_miss():
    _, _, n = image_entries([gt(GROUP, group=True), gt(A)], [])
    assert n == 2


def test_unverified_class_is_ignored_and_negative_class_is_all_false_positives():
    gts = {
        "unverified": {"verified": {"door": 1}, "boxes": [gt(B, "door")]},
        "negative": {"verified": {"window": 0}, "boxes": []},
    }
    preds = {"unverified": [det(A, 0.9)], "negative": [det(A, 0.8)]}
    e = class_entries(gts, preds, "window")
    assert e.num_images == 1
    assert e.num_gt == 0
    assert list(e.scores) == [0.8]
    assert list(e.tp) == [False]


def test_merged_class_needs_both_verified_and_pools_boxes():
    gts = {
        "both": {"verified": {"window": 1, "door": 0}, "boxes": [gt(A)]},
        "window_only": {"verified": {"window": 1}, "boxes": [gt(A)]},
        "mixed": {"verified": {"window": 1, "door": 1}, "boxes": [gt(A), gt(B, "door")]},
    }
    preds = {
        "both": [det(A, 0.9, "window")],
        "window_only": [det(B, 0.9, "window")],
        "mixed": [det(A, 0.8, "door"), det(B, 0.7, "window")],
    }
    e = class_entries(gts, preds, MERGED)
    assert e.num_images == 2
    assert e.num_gt == 3
    assert sorted(e.tp.tolist()) == [True, True, True]


def test_average_precision_matches_hand_computed_envelope():
    # Ranked TP, FP, TP with 3 GT: points (1/3, 1), (1/3, 1/2), (2/3, 2/3); envelope gives
    # 1/3 * 1 + 1/3 * 2/3 = 5/9.
    e = ClassEntries(np.array([0.9, 0.8, 0.7]), np.array([True, False, True]), num_gt=3, num_images=1)
    assert average_precision(e) == pytest.approx(5 / 9)


def test_threshold_is_lowest_score_meeting_precision():
    e = ClassEntries(np.array([0.9, 0.8, 0.7, 0.6, 0.5]), np.array([True, True, False, True, False]), 4, 1)
    t, rule = choose_threshold(e, 0.6)
    # precision by cut: 1, 1, 2/3, 3/4, 3/5 -> lowest cut still >= 0.6 is 0.5
    assert t == 0.5
    assert rule.startswith("lowest")
    r = at_threshold(e, t)
    assert (r["tp"], r["fp"], r["recall"]) == (3, 2, 0.75)


def test_threshold_skips_cuts_inside_tied_scores():
    # Two entries at 0.5: a cut can only fall above or below both.
    e = ClassEntries(np.array([0.9, 0.5, 0.5]), np.array([True, False, False]), 1, 1)
    t, _ = choose_threshold(e, 0.6)
    assert t == 0.9


def test_threshold_falls_back_to_best_f1():
    e = ClassEntries(np.array([0.9, 0.8]), np.array([False, True]), 1, 1)
    t, rule = choose_threshold(e, 0.6)
    assert t == 0.8
    assert "F1" in rule
    assert math.isnan(average_precision(ClassEntries(np.array([]), np.array([], bool), 0, 1)))


def test_detection_on_a_difficult_box_is_ignored_and_the_box_is_not_a_miss():
    hard = dict(gt(A), difficult=True)
    scores, tp, n = image_entries([hard, gt(B)], [det(A, 0.9), det(B, 0.8)])
    assert (scores, tp, n) == ([0.8], [True], 1)


def test_near_sized_filter_marks_boxes_below_the_minimum_side():
    tiny = [0.1, 0.1, 0.15, 0.4]  # 5% wide
    gts = {"i": {"verified": {"window": 1}, "boxes": [gt(tiny), gt(B)]}}
    near_miss = [0.0, 0.0, 0.2, 0.2]
    preds = {"i": [det(tiny, 0.9), det([0.0, 0.0, 0.05, 0.05], 0.8), det(near_miss, 0.7)]}
    e = class_entries(gts, preds, "window", near_min_side=0.10)
    assert e.num_gt == 1
    # the tiny detection is dropped by size, the near-sized miss stays a false positive
    assert list(e.scores) == [0.7]
    assert list(e.tp) == [False]


def test_choose_threshold_on_an_empty_set_reports_no_detections():
    e = ClassEntries(np.array([]), np.array([], bool), 0, 1)
    t, rule = choose_threshold(e)
    assert t == float("inf")
    assert rule == "no-detections"
    r = at_threshold(e, t)
    assert (r["tp"], r["fp"]) == (0, 0)
    assert math.isnan(r["precision"])
    assert math.isnan(r["recall"])


def test_average_precision_is_zero_when_nothing_is_detected():
    e = ClassEntries(np.array([]), np.array([], bool), 2, 1)
    assert average_precision(e) == 0.0


def test_difficult_group_is_neither_hit_nor_miss():
    hard_group = dict(gt(GROUP, group=True), difficult=True)
    scores, tp, n = image_entries([hard_group], [det([0.1, 0.6, 0.2, 0.7], 0.9)])
    assert (scores, tp, n) == ([], [], 0)


def test_image_missing_from_predictions_counts_all_misses():
    gts = {"only": {"verified": {"window": 1}, "boxes": [gt(A), gt(GROUP, group=True)]}}
    e = class_entries(gts, {}, "window")
    assert e.num_images == 1
    assert e.num_gt == 2
    assert len(e.scores) == 0
    assert average_precision(e) == 0.0
