"""The scored image sets and the prediction cache shared by every candidate.

Predictions live outside git at PREDS/<model>/<set>.json:
{"meta": {...}, "images": {image_id: {"elapsed_ms": float, "dets": [{"label", "score", "box"}]}}}
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PIL import Image

from .paths import CMP, DATA, ELECTRO, OI, PREDS

SETS = ("oi_tune", "oi_eval", "cmp")
# Prediction-only set for the 3D-extent step: the eight ETH3D electro photos, downscaled to 1024 px
# on the long side (python -m autodetect.sets). No 2D ground truth.
ELECTRO_1024 = DATA / "electro_1024"


def ground_truth(name: str) -> dict[str, dict]:
    if name == "electro":
        photos = json.loads((ELECTRO / "manifest.json").read_text())["photos"]
        return {p["id"]: {"verified": {}, "boxes": []} for p in photos}
    if name == "cmp":
        return json.loads((CMP / "gt.json").read_text())
    if name in ("oi_tune", "oi_eval", "oi_train"):
        return json.loads((OI / f"gt_{name[3:]}.json").read_text())
    raise KeyError(f"unknown set {name!r}; expected one of {SETS} or oi_train")


def image_path(name: str, image_id: str) -> Path:
    if name == "electro":
        return ELECTRO_1024 / f"{image_id}.jpg"
    if name == "cmp":
        return CMP / "base" / f"{image_id}.jpg"
    return OI / name[3:] / f"{image_id}.jpg"


def pred_path(model: str, name: str) -> Path:
    return PREDS / model / f"{name}.json"


def save_preds(model: str, name: str, meta: dict, images: dict) -> None:
    """Also records the 1, 5 and 15 minute load averages at save time: the Mac is shared, and a
    loaded machine slows every timing."""
    meta = dict(meta, load_avg_at_save=[round(x, 1) for x in os.getloadavg()])
    p = pred_path(model, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"meta": meta, "images": images}))


def load_preds(model: str, name: str) -> dict:
    return json.loads(pred_path(model, name).read_text())


def prepare_electro() -> None:
    photos = json.loads((ELECTRO / "manifest.json").read_text())["photos"]
    ELECTRO_1024.mkdir(parents=True, exist_ok=True)
    for photo in photos:
        with Image.open(ELECTRO / photo["image"]["path"]) as image:
            image.thumbnail((1024, 1024))
            image.convert("RGB").save(image_path("electro", photo["id"]), quality=95)


if __name__ == "__main__":
    prepare_electro()
