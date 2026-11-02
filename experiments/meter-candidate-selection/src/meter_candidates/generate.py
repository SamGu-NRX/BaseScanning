"""Freeze the experiment's inputs: synthetic meter-label images and hand-authored
observation sets.

Freeze discipline: the committed files under inputs/ are the experiment's only reader
inputs, and regenerating them from this module must reproduce every byte (test_inputs).
The images are synthetic plates drawn with Pillow from the literal specs below; the
observation sets are literal reader-shaped JSON ({lines: [{text, box}], barcodes: [...]})
hand-authored to cover clean reads, competitive distractors, a partial barcode read, a
vertical serial, a garbled read, and no-serial plates. Neither reader input carries gold:
serials live only in gold/labels.json (gold.py).

Every serial here is synthetic, so plaintext labels are safe — meter_eval.match's keyed
HMAC exists because real meter numbers may not be committed, and this experiment has no
real number, no HMAC key, and no calibrated threshold.
"""

import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

SIZE = (640, 360)
BG = (233, 232, 228)
INK = (28, 28, 30)
FAINT = (110, 110, 112)
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")

# The synthetic serials. Fake by construction (no meter_eval digest, no real identifier),
# fixed literals so the frozen files never depend on a random draw.
SERIALS = {
    "gen01": "48201637",
    "gen02": "73150492",
    "gen03": "20984615",
    "gen04": "66403728",
    "gen05": "90172634",
    "gen06": "55832176",
    "gen07": "39218407",
    "gen08": "77051263",
    "gen09": "14509682",
    "gen10": "60934285",
    "gen11": "82761340",
    "gen12": "33402798",
    "gen13": "57381904",
    "gen14": "68109327",
    "gen15": "82645013",
    "obs01": "24681097",
    "obs02": "90274815",
    "obs03": "50392846",
    "obs04": "36472950",
    "obs05": "73841095",
    "obs06": "24681357",
    "obs07": "55551024",
    "obs08": "61920734",
    "obs09": "48201637",
}

# Rendering spec per plate. The common plate prints a nameplate header, the serial after a
# No. label, and the spec lines a meter carries; variants degrade the print or add the
# distractor lines a candidate ranker must see off.
SPEC_LINES = ["20(60)A 50Hz", "CL 200", "FORM 9S"]
PLATES: dict[str, dict] = {
    **{f"gen{i:02d}": {"kind": "clean", "serial_size": 40} for i in range(1, 7)},
    **{
        f"gen{i:02d}": {"kind": "clean", "serial_size": 20, "contrast": 0.5, "noise": 4000}
        for i in range(7, 13)
    },
    "gen13": {"kind": "zeros", "serial_size": 36},
    "gen14": {"kind": "longdigits", "serial_size": 36, "vertical": True},
    "gen15": {"kind": "shadow", "serial_size": 28},
    "gen16": {"kind": "no-serial", "serial_size": 40},
    "gen17": {"kind": "no-serial", "serial_size": 20, "contrast": 0.5, "noise": 4000},
    "gen18": {"kind": "no-serial-zeros", "serial_size": 40},
}

FONT_PATHS = {
    False: FONT_DIR / "DejaVuSans.ttf",
    True: FONT_DIR / "DejaVuSans-Bold.ttf",
    "mono": FONT_DIR / "DejaVuSansMono.ttf",
}


def _font(bold: bool | str, size: int):
    path = FONT_PATHS["mono"] if bold == "mono" else FONT_PATHS[bool(bold)]
    return ImageFont.truetype(str(path), size)


def _text(draw: ImageDraw.ImageDraw, spec: dict) -> None:
    draw.text((spec["x"], spec["y"]), spec["text"], font=_font(spec["bold"], spec["size"]),
              fill=spec.get("fill", INK))


def _vertical(img: Image.Image, spec: dict) -> None:
    """Paste text rotated a quarter-turn, as a vertical-printed line would appear."""
    font = _font(spec["bold"], spec["size"])
    tile = Image.new("RGB", (font.size * 12, font.size + 8), BG)
    ImageDraw.Draw(tile).text((2, 2), spec["text"], font=font, fill=INK)
    rotated = tile.rotate(90, expand=True)
    rotated = rotated.crop(rotated.getbbox())
    img.paste(rotated, (spec["x"], spec["y"]))


def plate_elements(plate_id: str) -> tuple[list[dict], dict]:
    """The literal elements of one plate, plus its degradations."""
    spec = PLATES[plate_id]
    serial = SERIALS.get(plate_id)
    size = spec["serial_size"]
    elements: list[dict] = [{"text": "WATTHOUR METER", "size": 18, "bold": False, "x": 40,
                             "y": 18, "fill": FAINT}]
    if spec["kind"] == "zeros":
        elements.append({"text": "00000000", "size": 44, "bold": True, "x": 60, "y": 70})
    if spec["kind"] == "longdigits":
        elements.append({"text": "01234567890123", "size": 24, "bold": "mono", "x": 60, "y": 72})
    if spec["kind"] == "no-serial-zeros":
        elements.append({"text": "00000000", "size": 40, "bold": True, "x": 60, "y": 120})
    if serial and spec["kind"] != "shadow":
        elements.append({"text": f"No. {serial}", "size": size, "bold": True, "x": 60, "y": 150})
    if spec.get("vertical"):
        elements.append({"text": "SERIAL", "size": 18, "bold": False, "x": 570, "y": 60,
                         "vertical": True})
    if spec["kind"] == "shadow":
        # Two keyword lines, the taller one a decoy serial: a ranking case, not a
        # recognition one. Gold is the smaller print at gen15.
        elements.append({"text": f"No. {serial}", "size": 28, "bold": True, "x": 60, "y": 180})
        elements.append({"text": "No. 41529068", "size": 44, "bold": True, "x": 60, "y": 90})
    y = 232 if serial else 150
    for i, line in enumerate(SPEC_LINES):
        elements.append({"text": line, "size": 18, "bold": False, "x": 60 + 140 * (i % 2),
                         "y": y + 30 * (i // 2), "fill": FAINT})
    return elements, spec


def render_plate(plate_id: str) -> Image.Image:
    elements, spec = plate_elements(plate_id)
    img = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(img)
    for element in elements:
        if element.get("vertical"):
            _vertical(img, element)
        else:
            _text(draw, element)
    if spec.get("contrast", 1.0) < 1.0:
        img = ImageEnhance.Contrast(img).enhance(spec["contrast"])
    if spec.get("noise"):
        rng = random.Random(f"noise:{plate_id}")
        for _ in range(spec["noise"]):
            gray = rng.randrange(120, 200)
            draw.point((rng.randrange(SIZE[0]), rng.randrange(SIZE[1])), fill=(gray,) * 3)
    return img


# Hand-authored observation sets: reader-shaped JSON, written as literals. Boxes are
# [x, y, w, h] in arbitrary pixels; heights matter to the ranking features. obs09 is the
# garbled read ("O" for "0") a recognition failure produces; obs10-obs12 have no serial on
# the plate at all.
OBSERVATIONS: dict[str, dict] = {
    "obs01": {
        "lines": [
            {"text": "WATTHOUR METER", "box": [40, 20, 210, 22]},
            {"text": "No. 24681097", "box": [60, 120, 330, 46]},
            {"text": "20(60)A 50Hz", "box": [60, 220, 190, 22]},
            {"text": "CL 200", "box": [60, 260, 96, 20]},
            {"text": "FORM 9S", "box": [300, 260, 100, 20]},
        ],
        "barcodes": [],
    },
    "obs02": {
        "lines": [
            {"text": "S/N 90274815", "box": [60, 130, 300, 40]},
            {"text": "15(90)A 60Hz", "box": [60, 220, 190, 22]},
            {"text": "CL 200", "box": [60, 260, 96, 20]},
        ],
        "barcodes": [],
    },
    "obs03": {
        "lines": [
            {"text": "No. 5039 2846", "box": [60, 120, 340, 44]},
            {"text": "20(60)A 50Hz", "box": [60, 220, 190, 22]},
        ],
        "barcodes": [],
    },
    "obs04": {
        "lines": [
            {"text": "*B36472950*", "box": [60, 130, 280, 36]},
            {"text": "20(60)A 50Hz", "box": [60, 220, 190, 22]},
        ],
        "barcodes": [{"payload": "36472950", "box": [60, 130, 280, 36]}],
    },
    "obs05": {
        "lines": [
            {"text": "00000000", "box": [40, 60, 400, 60]},
            {"text": "No. 73841095", "box": [60, 170, 310, 30]},
            {"text": "CL 200", "box": [60, 260, 96, 20]},
        ],
        "barcodes": [],
    },
    "obs06": {
        "lines": [
            {"text": "No. 24681357", "box": [60, 170, 300, 28]},
            {"text": "No. 13572468", "box": [60, 80, 360, 52]},
        ],
        "barcodes": [],
    },
    "obs07": {
        "lines": [
            {"text": "WATTHOUR METER", "box": [40, 20, 210, 22]},
            {"text": "20(60)A 50Hz", "box": [60, 220, 190, 22]},
            {"text": "No. 55551024", "box": [520, 80, 34, 160]},
        ],
        "barcodes": [],
    },
    "obs08": {
        "lines": [
            {"text": "6192 073", "box": [60, 130, 260, 36]},
            {"text": "20(60)A 50Hz", "box": [60, 220, 190, 22]},
        ],
        "barcodes": [{"payload": "61920734", "box": [60, 130, 260, 36]}],
    },
    "obs09": {
        "lines": [
            {"text": "No. 482O1637", "box": [60, 120, 330, 46]},
            {"text": "20(60)A 50Hz", "box": [60, 220, 190, 22]},
        ],
        "barcodes": [],
    },
    "obs10": {
        "lines": [
            {"text": "20(60)A 50Hz", "box": [60, 220, 190, 22]},
            {"text": "CL 200", "box": [60, 260, 96, 20]},
            {"text": "FORM 9S", "box": [300, 260, 100, 20]},
            {"text": "0.3 KH", "box": [60, 180, 90, 20]},
        ],
        "barcodes": [],
    },
    "obs11": {"lines": [], "barcodes": []},
    "obs12": {
        "lines": [{"text": "KWH", "box": [60, 220, 70, 20]}],
        "barcodes": [{"payload": "AB12", "box": [300, 120, 80, 30]}],
    },
}

# Gold labels: the serials above, by case, with plate origin. Written to gold/, never to
# the reader input.
GOLD_ORIGIN = {"gen": "generated plate", "obs": "hand-authored observation set"}


def gold() -> dict:
    labels: dict[str, dict] = {}
    for plate_id in PLATES:
        serial = SERIALS.get(plate_id)
        labels[plate_id] = {
            "serial": serial,
            "present": serial is not None,
            "origin": GOLD_ORIGIN["gen"],
        }
    for case_id in OBSERVATIONS:
        serial = SERIALS.get(case_id)
        labels[case_id] = {
            "serial": serial,
            "present": serial is not None,
            "origin": GOLD_ORIGIN["obs"],
        }
    return labels


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def write_inputs(inputs_dir: Path, gold_path: Path) -> list[Path]:
    """Write every frozen file; returns the paths, for the freeze test."""
    written: list[Path] = []
    images = inputs_dir / "images"
    images.mkdir(parents=True, exist_ok=True)
    for plate_id in sorted(PLATES):
        path = images / f"{plate_id}.png"
        render_plate(plate_id).save(path, format="PNG")
        written.append(path)
    for case_id, obs in sorted(OBSERVATIONS.items()):
        path = inputs_dir / "observations" / f"{case_id}.json"
        _write_json(path, obs)
        written.append(path)
    _write_json(gold_path, gold())
    written.append(gold_path)
    return written
