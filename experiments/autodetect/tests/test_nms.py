import numpy as np
import pytest

from autodetect import config
from autodetect.nms import nms, postprocess


def test_nms_drops_lower_scoring_overlap():
    # IoU of the two boxes is 0.9 / 1.1, well above config.NMS_IOU.
    boxes = np.array([[0.0, 0.0, 1.0, 1.0], [0.1, 0.0, 1.1, 1.0]])
    assert nms(boxes, np.array([0.9, 0.8]), 0.5) == [0]


def test_nms_returns_indices_in_score_order():
    boxes = np.array([[5.0, 5.0, 6.0, 6.0], [0.0, 0.0, 1.0, 1.0]])
    assert nms(boxes, np.array([0.4, 0.9]), 0.5) == [1, 0]


def test_nms_ties_keep_the_earlier_index():
    boxes = np.array([[0.0, 0.0, 1.0, 1.0], [0.0, 0.0, 1.0, 1.0]])
    assert nms(boxes, np.array([0.5, 0.5]), 0.5) == [0]


def test_nms_keeps_iou_exactly_at_threshold():
    # Areas 1 and 0.5, intersection 0.5, union 1.0, IoU 0.5.
    boxes = np.array([[0.0, 0.0, 1.0, 1.0], [0.0, 0.0, 1.0, 0.5]])
    assert nms(boxes, np.array([0.9, 0.8]), 0.5) == [0, 1]
    assert nms(boxes, np.array([0.9, 0.8]), 0.49) == [0]


def test_nms_empty_input_returns_empty():
    assert nms(np.zeros((0, 4)), np.zeros(0), 0.5) == []


def test_postprocess_overlapping_other_class_survives():
    # The door overlaps both windows with IoU about 0.9 and still survives.
    boxes = np.array([[0.0, 0.0, 1.0, 1.0], [0.1, 0.0, 1.1, 1.0], [0.05, 0.0, 1.05, 1.0]])
    det = postprocess(boxes, np.array([0.9, 0.8, 0.7]), ["window", "window", "door"])
    assert [(str(d["label"]), d["score"]) for d in det] == [("window", 0.9), ("door", 0.7)]
    assert det[0]["box"] == [0.0, 0.0, 1.0, 1.0]


def test_postprocess_floor_drops_below_min_score():
    boxes = np.array([[0.0, 0.0, 1.0, 1.0], [2.0, 0.0, 3.0, 1.0]])
    scores = np.array([config.MIN_SCORE, config.MIN_SCORE - 0.005])
    det = postprocess(boxes, scores, ["window", "door"])
    assert [str(d["label"]) for d in det] == ["window"]


def test_postprocess_caps_at_max_dets():
    n = config.MAX_DETS + 1
    boxes = []
    for i in range(n):
        x = (i % 11) * 0.09
        y = (i // 11) * 0.09
        boxes.append([x, y, x + 0.05, y + 0.05])
    scores = np.linspace(0.99, 0.01, n)
    det = postprocess(np.array(boxes), scores, ["window"] * n)
    assert len(det) == config.MAX_DETS
    assert min(d["score"] for d in det) == pytest.approx(float(scores[config.MAX_DETS - 1]))


def test_postprocess_ties_keep_class_name_order():
    boxes = np.array([[0.0, 0.0, 1.0, 1.0], [2.0, 0.0, 3.0, 1.0]])
    det = postprocess(boxes, np.array([0.6, 0.6]), ["window", "door"])
    assert [str(d["label"]) for d in det] == ["door", "window"]


def test_postprocess_accepts_float32_boxes():
    boxes = np.array([[0.0, 0.0, 1.0, 1.0], [0.1, 0.0, 1.1, 1.0]], dtype=np.float32)
    scores = np.array([0.9, 0.8], dtype=np.float32)
    det = postprocess(boxes, scores, ["window", "window"])
    assert len(det) == 1
    assert det[0]["box"] == [0.0, 0.0, 1.0, 1.0]


def test_postprocess_empty_input_returns_empty():
    assert postprocess(np.zeros((0, 4)), np.zeros(0), []) == []
