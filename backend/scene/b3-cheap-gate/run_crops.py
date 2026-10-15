"""Reading tier on crops: B2's unchanged prompts, OpenAI / xAI models only.

    B2_DATA=/tmp/b3/data python run_crops.py --set meter|panel|extra --model gpt-5.5 --crops results/crops_owl.json [--max-side 1024]

Each photo is cropped to its box (full frame when the box is null) and downscaled so the long side
is at most --max-side (B2 sent full frames at 2048). Output is B2's jsonl format, so tiers.py scores
it unchanged. Meter answers contain real meter numbers: they go to $B3_B2RAW/meter_crop/ (copied
into internal/breadth/b2/b3-raw/ afterwards); panel answers go to $B3_RAW/<set>_crop/.
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
sys.path.insert(0, str(HERE.parent / "b2"))
import vlm as b2vlm  # noqa: E402

DATA = Path(os.environ.get("B2_DATA", "/tmp/b3/data"))


def encode(path, box, max_side):
    with Image.open(path) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
    if box:
        w, h = img.size
        img = img.crop((round(box[0] * w), round(box[1] * h), round(box[2] * w), round(box[3] * h)))
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode(), img.size


def run_one(model, set_name, path, box, max_side):
    b64, size = encode(path, box, max_side)
    for attempt in range(4):
        t = time.time()
        try:
            text, usage = b2vlm.call(model, b2vlm.PROMPTS[set_name], b64)
            ans = b2vlm.parse(text)
            return {"id": path.stem, "engine": f"vlm_{model}_crop", "secs": round(time.time() - t, 2), "usage": usage,
                    "size": list(size), "cropped": bool(box), "answer": ans, "raw_text": text if ans is None else ""}
        except Exception as e:  # noqa: BLE001
            if attempt == 3:
                return {"id": path.stem, "engine": f"vlm_{model}_crop", "error": str(e)[:300]}
            time.sleep(10 * (attempt + 1))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--set", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--crops", required=True)
    p.add_argument("--max-side", type=int, default=1024)
    p.add_argument("--tag", default="")
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args()
    boxes = json.loads(Path(args.crops).read_text())
    images = [x for x in sorted((DATA / args.set / "images").glob("*.jpg")) if f"{args.set}/{x.stem}" in boxes]
    root = Path(os.environ.get("B3_B2RAW", "/tmp/b3/b2raw")) if args.set == "meter" else Path(os.environ.get("B3_RAW", "/tmp/b3/raw"))
    out = root / f"{args.set}_crop{args.tag}" / f"vlm_{args.model}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(x)["id"] for x in out.read_text().splitlines() if x.strip() and '"error"' not in x} if out.exists() else set()
    todo = [x for x in images if x.stem not in done]
    print(args.model, args.set, len(todo), flush=True)
    with out.open("a") as h, ThreadPoolExecutor(args.workers) as pool:
        for rec in pool.map(lambda x: run_one(args.model, args.set, x, boxes[f"{args.set}/{x.stem}"], args.max_side), todo):
            h.write(json.dumps(rec, ensure_ascii=False) + "\n")
            h.flush()
            if "error" in rec:
                print("ERR", rec["id"], rec["error"], flush=True)


if __name__ == "__main__":
    main()
