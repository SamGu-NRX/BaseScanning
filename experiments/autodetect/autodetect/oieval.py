"""Open Images detection scoring at IoU 0.5, following the official OID challenge protocol
(TensorFlow Object Detection API, `per_image_evaluation.py` in its Open Images mode).

- A class is scored in an image only when a person verified that class there. Detections of an
  unverified class are ignored, neither true nor false positives, and its boxes are not counted.
- Verified negative: no ground truth, so every detection of the class is a false positive.
- Detections match non-group boxes first, in score order, at IoU >= 0.5 against the box they
  overlap most; a second detection of an already-matched box is a false positive.
- A detection left unmatched whose intersection with a group-of box covers >= 50% of the
  detection's own area joins that group. Each group yields one true positive, scored with the
  highest score among its detections, and absorbs the rest without false positives. A group
  nobody detected counts as one missed object.

Ground truth per image: {"verified": {cls: 1 | 0}, "boxes": [{"label", "box", "group"}]}.
Detections per image: [{"label", "score", "box"}]. Boxes are [x0, y0, x1, y1], normalized.

A box marked "difficult" (used only by the near-sized diagnostic, for boxes too small to be what
the app sees at 1 to 3 m) is not counted as a miss, and a detection matched to it is ignored, as
the protocol treats difficult boxes.

The class "window_or_door" merges both classes. It is scored only in images where both Window
and Door were verified, since otherwise an unboxed door could turn a correct detection into a
false positive.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

IOU = 0.5  # match threshold: IoU for single boxes, IoA over the detection for groups
MERGED = "window_or_door"  # pooled class, scored only where both members were verified
MERGED_FROM = ("window", "door")  # the two classes MERGED pools


@dataclass
class ClassEntries:
    """Scored entries for one class over a set: one per counted detection or detected group.

    scores and tp align by position and are not globally sorted. num_gt counts missable
    ground truth objects, detected or not.
    """

    scores: np.ndarray  # float
    tp: np.ndarray  # bool
    num_gt: int
    num_images: int  # images where the class was scored


def _iou(d: np.ndarray, g: np.ndarray) -> np.ndarray:
    """IoU between each box in d (rows) and each box in g (columns)."""
    ix = np.clip(np.minimum(d[:, None, 2], g[None, :, 2]) - np.maximum(d[:, None, 0], g[None, :, 0]), 0, None)
    iy = np.clip(np.minimum(d[:, None, 3], g[None, :, 3]) - np.maximum(d[:, None, 1], g[None, :, 1]), 0, None)
    inter = ix * iy
    ad = (d[:, 2] - d[:, 0]) * (d[:, 3] - d[:, 1])
    ag = (g[:, 2] - g[:, 0]) * (g[:, 3] - g[:, 1])
    return inter / np.maximum(ad[:, None] + ag[None, :] - inter, 1e-12)


def _ioa_det(d: np.ndarray, g: np.ndarray) -> np.ndarray:
    """Intersection over the detection's area."""
    ix = np.clip(np.minimum(d[:, None, 2], g[None, :, 2]) - np.maximum(d[:, None, 0], g[None, :, 0]), 0, None)
    iy = np.clip(np.minimum(d[:, None, 3], g[None, :, 3]) - np.maximum(d[:, None, 1], g[None, :, 1]), 0, None)
    ad = (d[:, 2] - d[:, 0]) * (d[:, 3] - d[:, 1])
    return ix * iy / np.maximum(ad[:, None], 1e-12)


def image_entries(gt_boxes: list[dict], dets: list[dict], iou_thr: float = IOU) -> tuple[list[float], list[bool], int]:
    """Score one class in one image where it is verified. Inputs must hold that class only.

    gt_boxes are that class's ground truth boxes, dets its detections. Returns (scores, tp,
    num_gt): one scores/tp pair per counted detection plus one per detected group, whose
    score is the max score among its detections and whose tp is True. num_gt counts
    non-difficult singles plus non-difficult groups, detected or not. Detections matched to
    difficult boxes or absorbed by groups are dropped. Scores follow detection score order,
    with group entries last.

    >>> s, tp, n = image_entries(
    ...     [{"label": "window", "box": [0.0, 0.0, 0.5, 0.5], "group": False}],
    ...     [{"label": "window", "score": 0.9, "box": [0.0, 0.0, 0.5, 0.5]},
    ...      {"label": "window", "score": 0.8, "box": [0.0, 0.0, 0.5, 0.5]}],
    ... )
    >>> [float(x) for x in s]
    [0.9, 0.8]
    >>> [bool(x) for x in tp]
    [True, False]
    >>> n
    1
    """
    order = sorted(range(len(dets)), key=lambda i: -dets[i]["score"])
    scores = np.array([dets[i]["score"] for i in order], dtype=float)
    d = np.array([dets[i]["box"] for i in order], dtype=float).reshape(-1, 4)
    singles = [b for b in gt_boxes if not b["group"]]
    grouped = [b for b in gt_boxes if b["group"]]
    single = np.array([b["box"] for b in singles], dtype=float).reshape(-1, 4)
    groups = np.array([b["box"] for b in grouped], dtype=float).reshape(-1, 4)
    single_hard = np.array([b.get("difficult", False) for b in singles], dtype=bool)
    group_hard = np.array([b.get("difficult", False) for b in grouped], dtype=bool)
    n = len(scores)
    tp = np.zeros(n, dtype=bool)
    ignored = np.zeros(n, dtype=bool)  # matched to a difficult box, or absorbed by a group

    if len(single) and n:
        iou = _iou(d, single)
        best = iou.argmax(axis=1)
        taken = np.zeros(len(single), dtype=bool)
        for i in range(n):
            g = best[i]
            if iou[i, g] < iou_thr:
                continue
            if single_hard[g]:
                ignored[i] = True
            elif not taken[g]:
                tp[i] = True
                taken[g] = True

    group_score = np.zeros(len(groups))
    if len(groups) and n:
        ioa = _ioa_det(d, groups)
        best = ioa.argmax(axis=1)
        for i in range(n):
            g = best[i]
            if not tp[i] and not ignored[i] and ioa[i, g] >= iou_thr:
                ignored[i] = True
                group_score[g] = max(group_score[g], scores[i])

    keep = ~ignored
    hit_groups = (group_score > 0) & ~group_hard
    out_scores = list(scores[keep]) + list(group_score[hit_groups])
    out_tp = list(tp[keep]) + [True] * int(hit_groups.sum())
    return out_scores, out_tp, int((~single_hard).sum() + (~group_hard).sum())


def _view(gt: dict, dets: list[dict], cls: str) -> tuple[int | None, list[dict], list[dict]]:
    """(verification, gt boxes, detections) of one class in one image, merging for MERGED."""
    if cls == MERGED:
        v = [gt["verified"].get(c) for c in MERGED_FROM]
        status = None if None in v else int(max(v))
        members = set(MERGED_FROM)
    else:
        status = gt["verified"].get(cls)
        members = {cls}
    boxes = [b for b in gt["boxes"] if b["label"] in members]
    ds = [x for x in dets if x["label"] in members]
    return status, boxes, ds


def _mark_small(boxes: list[dict], min_side: float) -> list[dict]:
    """Copy of boxes with difficult set where the narrower or shorter side is under min_side."""
    return [dict(b, difficult=min(b["box"][2] - b["box"][0], b["box"][3] - b["box"][1]) < min_side) for b in boxes]


def class_entries(
    gts: dict[str, dict],
    preds: dict[str, list[dict]],
    cls: str,
    iou_thr: float = IOU,
    near_min_side: float | None = None,
) -> ClassEntries:
    """Score one class over a set of images.

    gts maps image id to its ground truth record; preds maps image id to that image's
    detections, and an image absent from preds counts as no detections. Images where the
    class is not verified are skipped: their detections count nothing and their boxes are
    not missed. Returns ClassEntries. With near_min_side, ground truth boxes narrower or
    shorter than that fraction of the image are difficult, and detections that small are
    dropped, as an app that only proposes near objects would drop them.
    """
    scores: list[float] = []
    tps: list[bool] = []
    num_gt = 0
    num_images = 0
    for image_id, gt in gts.items():
        status, boxes, dets = _view(gt, preds.get(image_id, []), cls)
        if status is None:
            continue  # not verified: detections ignored, boxes not counted
        num_images += 1
        if near_min_side is not None:
            boxes = _mark_small(boxes, near_min_side)
            dets = [x for x in dets if min(x["box"][2] - x["box"][0], x["box"][3] - x["box"][1]) >= near_min_side]
        s, t, n = image_entries(boxes if status == 1 else [], dets, iou_thr)
        scores += s
        tps += t
        num_gt += n
    return ClassEntries(np.array(scores, dtype=float), np.array(tps, dtype=bool), num_gt, num_images)


def average_precision(e: ClassEntries) -> float:
    """Area under the precision envelope over recall (all points), as in the OID challenge.

    Returns nan when there is no ground truth, and 0.0 when ground truth exists but nothing
    was scored. A trailing false positive adds no area: one true positive out of two objects
    scores 0.5.

    >>> e = ClassEntries(np.array([0.9, 0.8]), np.array([True, False]), num_gt=2, num_images=1)
    >>> average_precision(e)
    0.5
    >>> average_precision(ClassEntries(np.array([]), np.array([], bool), 0, 1))
    nan
    """
    if e.num_gt == 0:
        return float("nan")
    if len(e.scores) == 0:
        return 0.0
    order = np.argsort(-e.scores, kind="stable")
    tp = e.tp[order].astype(float)
    ctp = np.cumsum(tp)
    cfp = np.cumsum(1 - tp)
    recall = np.concatenate([[0.0], ctp / e.num_gt, [1.0]])
    precision = np.concatenate([[0.0], ctp / (ctp + cfp), [0.0]])
    for i in range(len(precision) - 2, -1, -1):
        precision[i] = max(precision[i], precision[i + 1])
    steps = np.where(recall[1:] != recall[:-1])[0] + 1
    return float(np.sum((recall[steps] - recall[steps - 1]) * precision[steps]))


def at_threshold(e: ClassEntries, t: float) -> dict:
    """Precision and recall when every entry with score >= t is kept.

    Returns threshold, tp, fp, num_gt, precision and recall in one dict. Precision is nan
    when no entry is kept; recall is nan when there is no ground truth.

    >>> e = ClassEntries(np.array([0.9, 0.8, 0.7, 0.6, 0.5]), np.array([True, True, False, True, False]), 4, 1)
    >>> at_threshold(e, 0.5)
    {'threshold': 0.5, 'tp': 3, 'fp': 2, 'num_gt': 4, 'precision': 0.6, 'recall': 0.75}
    """
    sel = e.scores >= t
    tp = int(e.tp[sel].sum())
    fp = int(sel.sum()) - tp
    return {
        "threshold": t,
        "tp": tp,
        "fp": fp,
        "num_gt": e.num_gt,
        "precision": tp / (tp + fp) if tp + fp else float("nan"),
        "recall": tp / e.num_gt if e.num_gt else float("nan"),
    }


def choose_threshold(e: ClassEntries, min_precision: float = 0.6) -> tuple[float, str]:
    """Operating threshold from a tuning set: the lowest score at which precision >= min_precision
    (the most recall that meets the precision bar). If no score reaches it, the score with the best
    F1. Returns (threshold, rule used). With no entries or no ground truth, (inf, "no-detections").

    >>> e = ClassEntries(np.array([0.9, 0.8, 0.7]), np.array([True, False, True]), 2, 1)
    >>> choose_threshold(e, 0.6)
    (0.7, 'lowest score with precision >= 0.6')
    >>> choose_threshold(ClassEntries(np.array([]), np.array([], bool), 0, 1))
    (inf, 'no-detections')
    """
    if len(e.scores) == 0 or e.num_gt == 0:
        return float("inf"), "no-detections"
    order = np.argsort(-e.scores, kind="stable")
    s = e.scores[order]
    tp = np.cumsum(e.tp[order])
    n = np.arange(1, len(s) + 1)
    # Only a cut between distinct scores is a real threshold; take the last index of each score.
    last = np.r_[s[1:] != s[:-1], True]
    s, tp, n = s[last], tp[last], n[last]
    precision = tp / n
    ok = np.where(precision >= min_precision)[0]
    if len(ok):
        return float(s[ok.max()]), f"lowest score with precision >= {min_precision:g}"
    f1 = 2 * tp / (n + e.num_gt)
    return float(s[f1.argmax()]), "best F1 (no score reaches the precision bar)"
