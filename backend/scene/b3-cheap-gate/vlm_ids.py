"""Meter reading with an identifier-context prompt (OpenAI / xAI only).

    B2_DATA=/tmp/b3/data python vlm_ids.py --model gpt-5.5|grok-4.7|gpt-5.4-mini [--crop crops.json] [--workers 8]

Same call path as B2's vlm.py, but the model lists every printed identifier with where it is printed
and what it is, then picks the utility meter number by the rules in Base's P4 prompt (utility-facing
number over manufacturer serial, strip barcode wrappers). Output keeps B2's `meter_number` /
`number_candidates` keys so B2's and B3's scorers work unchanged.

Raw answers contain real meter numbers, so they are written under internal/breadth/b2/b3-raw/
(meter numbers must not leave b2/). With --crop, photos are cropped to the given normalised box
first (see crop.py) and the tag "_crop" is added to the file name.
"""

import argparse
import base64
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
B2 = HERE.parent / "b2"
sys.path.insert(0, str(B2))
import vlm as b2vlm  # noqa: E402

DATA = Path(os.environ.get("B2_DATA", "/tmp/b3/data"))
# Write off-store while appending (store sync makes conflict copies), then copy into b2/b3-raw/.
OUT = Path(os.environ.get("B3_B2RAW", B2 / "b3-raw"))

PROMPT = """This is a photo of an electricity meter, taken for a utility site survey. We need the
utility's meter number (the id the electric utility uses for this meter).
Transcribe only what is printed and visible. Never guess characters you cannot see.

Step 1. List every identifier-like string printed on the meter or on labels attached to it
(numbers, serials, barcode text). For each give where it is printed and what it most likely is.
Step 2. Choose the utility meter number:
- Prefer the utility-facing meter number (often the largest print on a utility label or plate, next
  to a barcode, or after "No."/"Nr."/"#"/"Meter") over the manufacturer's serial, model or catalogue codes.
- If a barcode's printed text wraps a shorter number that is also printed on its own as the
  meter number (a prefix or suffix of letters or digits around it), return the shorter number.
- Strip letter prefixes that are utility wrappers (for example DARG272815824 -> 272815824).
- Exclude kWh readings, CL/class/form, volts/amps, Kh, FCC/UL ids, dates and account numbers.

Return one JSON object and nothing else:
{"identifiers": [{"text": "...", "where": "large print on faceplate | utility label | barcode sticker text | manufacturer nameplate | other", "kind": "utility_meter_number | manufacturer_serial | barcode_text | model_or_catalogue | other"}],
 "meter_number": "<chosen string or null if not legible>",
 "number_candidates": ["<chosen first>", "<next most likely meter number>", "..."],
 "ambiguous": true|false  (true if another listed identifier could plausibly be the utility number),
 "class_label": "ANSI class as printed (e.g. CL200) or, without CL, the current rating (e.g. 10(60)A), or null",
 "legibility": "good"|"partial"|"poor"}
"""


def encode(path: Path, box=None) -> str:
    with Image.open(path) as im:
        image = ImageOps.exif_transpose(im).convert("RGB")
    if box:
        w, h = image.size
        image = image.crop((round(box[0] * w), round(box[1] * h), round(box[2] * w), round(box[3] * h)))
    image.thumbnail((b2vlm.MAX_SIDE, b2vlm.MAX_SIDE))
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def run_one(model, path, box, tag):
    b64 = encode(path, box)
    for attempt in range(4):
        start = time.time()
        try:
            text, usage = b2vlm.call(model, PROMPT, b64)
            ans = b2vlm.parse(text)
            return {"id": path.stem, "engine": f"vlm_{model}{tag}", "secs": round(time.time() - start, 2), "usage": usage,
                    "answer": ans, "raw_text": text if ans is None else ""}
        except Exception as error:  # noqa: BLE001
            if attempt == 3:
                return {"id": path.stem, "engine": f"vlm_{model}{tag}", "error": str(error)[:300]}
            time.sleep(10 * (attempt + 1))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--crop", default="")
    p.add_argument("--ids", default="")
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args()
    boxes = json.loads(Path(args.crop).read_text()) if args.crop else {}
    tag = "_crop" if args.crop else ""
    images = sorted((DATA / "meter" / "images").glob("*.jpg"))
    if args.ids:
        images = [x for x in images if x.stem in set(args.ids.split(","))]
    out = OUT / ("meter_ids" + tag) / f"vlm_{args.model}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(line)["id"] for line in out.read_text().splitlines() if line.strip() and "error" not in line} if out.exists() else set()
    todo = [x for x in images if x.stem not in done]
    with out.open("a") as h, ThreadPoolExecutor(args.workers) as pool:
        for rec in pool.map(lambda x: run_one(args.model, x, boxes.get(f"meter/{x.stem}"), tag), todo):
            h.write(json.dumps(rec, ensure_ascii=False) + "\n")
            h.flush()
            print(args.model, rec["id"], rec.get("secs"), "ERR " + rec.get("error", "") if "error" in rec else "", flush=True)


if __name__ == "__main__":
    main()
