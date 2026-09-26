"""Shared class list, prompts and image IO for the B1 detection comparison."""

import json
from pathlib import Path

from PIL import Image, ImageOps

STORE = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2")
B1 = STORE / "internal/breadth/b1-detection"
WORK = Path("/home/ubuntu/b1")

# Criteria objects. `prompt` is the text query given to open-vocabulary detectors.
CLASSES = {
    "electric_meter": "electric meter",
    "meter_can": "electric meter socket box",
    "main_panel": "electrical breaker panel",
    "gas_meter": "gas meter",
    "ac_condenser": "air conditioner condenser unit",
    "window": "window",
    "door": "door",
    "downspout": "downspout",
    "hose_bib": "outdoor water faucet",
    "fence": "fence",
    "pool": "swimming pool",
    "driveway": "driveway",
}
CLASS_NAMES = list(CLASSES)
PROMPT_TO_CLASS = {v: k for k, v in CLASSES.items()}

GROUND_TYPES = ["grass", "gravel", "concrete_or_paving", "mulch_or_soil", "deck", "other"]

# Pre-registered matching IoU per class: extended or thin objects get a looser bar.
MATCH_IOU = {c: 0.5 for c in CLASS_NAMES}
MATCH_IOU.update({"downspout": 0.3, "fence": 0.3, "driveway": 0.3, "pool": 0.3})

MAX_SIDE = 1024


def load_image(path, max_side=MAX_SIDE):
    """Upright RGB image downscaled so the long side is at most max_side; returns (img, scale)."""
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    s = min(1.0, max_side / max(img.size))
    if s < 1.0:
        img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    return img, s


def read_image_list(path=B1 / "eval_images.json"):
    return json.loads(Path(path).read_text())


def write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def read_jsonl(path):
    return [json.loads(line) for line in open(path) if line.strip()]
