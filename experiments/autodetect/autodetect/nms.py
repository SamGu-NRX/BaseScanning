"""Shared post-processing for the open-vocabulary detectors: per-class NMS, a score floor and a
per-image cap, all from config."""

from __future__ import annotations

import numpy as np

from . import config


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    """Indices kept by greedy NMS, highest score first.

    boxes is an (n, 4) array of xyxy corners, scores is one float per box, and iou is the
    suppression threshold. The sort is stable, so equal scores keep input order and the
    earlier box wins. A box is dropped when its IoU with an already kept box exceeds iou.
    IoU exactly equal to iou survives. Returned indices point into boxes and arrive in
    descending score order, not position order. Empty input returns [].

    >>> boxes = np.array([[0.0, 0.0, 1.0, 1.0], [0.1, 0.0, 1.1, 1.0], [2.0, 0.0, 3.0, 1.0]])
    >>> nms(boxes, np.array([0.9, 0.8, 0.7]), 0.5)
    [0, 2]
    """
    order = np.argsort(-scores, kind="stable")
    keep: list[int] = []
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while len(order):
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        ix = np.clip(np.minimum(boxes[i, 2], boxes[rest, 2]) - np.maximum(boxes[i, 0], boxes[rest, 0]), 0, None)
        iy = np.clip(np.minimum(boxes[i, 3], boxes[rest, 3]) - np.maximum(boxes[i, 1], boxes[rest, 1]), 0, None)
        inter = ix * iy
        overlap = inter / np.maximum(area[i] + area[rest] - inter, 1e-12)
        order = rest[overlap <= iou]
    return keep


def postprocess(boxes: np.ndarray, scores: np.ndarray, labels: list[str]) -> list[dict]:
    """Filter raw detector output: score floor, per-class NMS, then a per-image cap.

    Keeps boxes scoring at least config.MIN_SCORE. Runs nms once per class at
    config.NMS_IOU, so boxes of different classes never suppress each other. Sorts the
    survivors by score descending and returns at most config.MAX_DETS of them. Each dict
    carries "label" (str), "score" (float) and "box" (four xyxy floats). Callers pass
    normalized corners in [0, 1] and one label per box, in the same order as scores. The
    final sort is stable, so tied scores keep the order the per-class loop appended.

    >>> boxes = np.array([[0.0, 0.0, 1.0, 1.0], [0.1, 0.0, 1.1, 1.0], [0.05, 0.0, 1.05, 1.0]])
    >>> det = postprocess(boxes, np.array([0.9, 0.8, 0.7]), ["window", "window", "door"])
    >>> [(str(d["label"]), d["score"]) for d in det]
    [('window', 0.9), ('door', 0.7)]
    """
    keep_floor = scores >= config.MIN_SCORE
    out: list[dict] = []
    labels_arr = np.array(labels)
    for cls in sorted(set(labels_arr[keep_floor])):
        idx = np.where(keep_floor & (labels_arr == cls))[0]
        for j in nms(boxes[idx], scores[idx], config.NMS_IOU):
            k = idx[j]
            out.append({"label": cls, "score": float(scores[k]), "box": [float(v) for v in boxes[k]]})
    out.sort(key=lambda d: -d["score"])
    return out[: config.MAX_DETS]
