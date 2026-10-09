"""The scored image sets and the prediction cache shared by every candidate.

A set name is one of SETS ("oi_tune", "oi_eval", "cmp"), "oi_train" (Open Images training
images, never scored) or "electro" (the eight ETH3D electro photos, no 2D ground truth, scored
in 3D only by extent.py). ground_truth reads a set's labels: {image_id: {"verified": {class:
1 | 0}, "boxes": [{"label", "box", "group"}]}}. image_path maps a set name and image id to
the image file.

Predictions live outside git at PREDS/<model>/<set>.json:
{"meta": {...}, "images": {image_id: {"elapsed_ms": float, "dets": [{"label", "score", "box"}]}}}
Detection boxes are [x0, y0, x1, y1], normalized to the image with a top-left origin, the same
frame as the ground truth. "meta" is whatever the producer records (owl.py's meta() is the
fullest example); save_preds adds "load_avg_at_save" to it. Per-image entries may carry extra
keys, for example owl.py also stores "preprocess_ms".
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .paths import CMP, DATA, ELECTRO, OI, PREDS

SETS = ("oi_tune", "oi_eval", "cmp")
# Prediction-only set for the 3D-extent step: the eight ETH3D electro photos, downscaled to 1024 px
# on the long side. No 2D ground truth. No committed code builds these copies.
ELECTRO_1024 = DATA / "electro_1024"


def ground_truth(name: str) -> dict[str, dict]:
    """Read a set's ground truth: {image_id: {"verified": {class: 1 | 0}, "boxes": [...]}}.

    "cmp" reads DATA/cmp/gt.json; "oi_tune", "oi_eval" and "oi_train" read DATA/oi/gt_<split>.json.
    "electro" has no 2D labels: every image gets {"verified": {}, "boxes": []}, so the photos
    enter scoring pipelines with nothing scored in 2D (extent.py lifts door edges in 3D itself).

    Raises KeyError for any other name.

    >>> ground_truth("nope")
    Traceback (most recent call last):
        ...
    KeyError: "unknown set 'nope'; expected one of ('oi_tune', 'oi_eval', 'cmp') or oi_train or electro"
    """
    if name == "electro":
        photos = json.loads((ELECTRO / "manifest.json").read_text())["photos"]
        return {p["id"]: {"verified": {}, "boxes": []} for p in photos}
    if name == "cmp":
        return json.loads((CMP / "gt.json").read_text())
    if name in ("oi_tune", "oi_eval", "oi_train"):
        return json.loads((OI / f"gt_{name[3:]}.json").read_text())
    raise KeyError(f"unknown set {name!r}; expected one of {SETS} or oi_train or electro")


def image_path(name: str, image_id: str) -> Path:
    """The file one image lives in, under the same roots ground_truth reads.

    "electro" maps to the 1024 px copies in DATA/electro_1024, not the packet's full photos.
    Any other name is treated as Open Images with the first three characters stripped, so a
    typo maps to a path that does not exist instead of raising.

    >>> image_path("oi_eval", "abc").parts[-3:]
    ('oi', 'eval', 'abc.jpg')
    >>> image_path("electro", "abc").parts[-2:]
    ('electro_1024', 'abc.jpg')
    >>> image_path("cmp", "abc").parts[-3:]
    ('cmp', 'base', 'abc.jpg')
    """
    if name == "electro":
        return ELECTRO_1024 / f"{image_id}.jpg"
    if name == "cmp":
        return CMP / "base" / f"{image_id}.jpg"
    return OI / name[3:] / f"{image_id}.jpg"


def pred_path(model: str, name: str) -> Path:
    """The cache file for one model's predictions on one set: PREDS/<model>/<name>.json.

    >>> pred_path("owlv2", "oi_eval").as_posix().endswith("preds/owlv2/oi_eval.json")
    True
    """
    return PREDS / model / f"{name}.json"


def save_preds(model: str, name: str, meta: dict, images: dict) -> None:
    """Write one model's predictions for one set, creating the directory if needed.

    The file is {"meta": meta, "images": images}. meta is copied, never mutated, and gains
    "load_avg_at_save": the 1, 5 and 15 minute load averages (os.getloadavg) rounded to 0.1,
    read at save time. The Mac was shared, and a loaded machine slows every timing; score.py
    prints them beside the latency numbers.
    """
    meta = dict(meta, load_avg_at_save=[round(x, 1) for x in os.getloadavg()])
    p = pred_path(model, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"meta": meta, "images": images}))


def load_preds(model: str, name: str) -> dict:
    """Read a cache file back as {"meta": ..., "images": {...}}.

    Raises FileNotFoundError before the model's first run on that set.
    """
    return json.loads(pred_path(model, name).read_text())
