"""Ask a vision-language model to find the criteria objects in each evaluation image.

One call per image, all classes at once. Writes results/vlm/<model>.jsonl with the same `dets` shape
as run_detectors.py (label, score, normalised xyxy box) plus the model's per-class presence answers,
ground type, token usage and wall-clock seconds.

usage: python run_vlm.py <model> [--images eval_images_batch1.json] [--limit N] [--workers 4]
models: claude-sonnet-5 claude-opus-5-5 claude-haiku-4-5-20251001 gpt-5.5 gpt-5.4-mini grok-4.7
"""

import argparse
import base64
import io
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
import openai

from common import B1, CLASSES, CLASS_NAMES, GROUND_TYPES, load_image, read_image_list, read_jsonl, write_jsonl

CLASS_TEXT = "\n".join(f"- {k}: {v}" for k, v in CLASSES.items())
PROMPT = f"""You are inspecting a photo for a home battery installation survey. Find every visible instance of these object classes:
{CLASS_TEXT}

Notes: meter_can is the metal socket enclosure an electric meter plugs into (box it separately from the meter glass). main_panel is an electrical breaker/distribution panel or its closed enclosure. hose_bib is an outdoor water tap/spigot. driveway is a paved/gravel vehicle driveway area. A water meter is not a gas meter. Only box objects you can actually see; do not guess hidden ones. Box each window/door individually.

Also say, for each class, whether at least one instance is visible ("yes", "no" or "unsure"), and the dominant ground surface visible outdoors near the building, one of {GROUND_TYPES} (use "other" if no ground is visible).

Reply with JSON only, no prose:
{{"objects": [{{"label": "<class>", "box_2d": [ymin, xmin, ymax, xmax], "confidence": 0.0-1.0}}],
 "present": {{"<class>": "yes|no|unsure", ...}},
 "ground_type": "<ground type>"}}
box_2d coordinates are integers 0-1000 relative to image height (y) and width (x)."""

# USD per million tokens (input, output). Sources in B1-detection.md section 4.
PRICES = {
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus-5-5": (5.0, 25.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "gpt-5.5": (5.0, 30.0),
    "gpt-5.4-mini": (0.75, 4.5),
    "grok-4.7": (None, None),  # xAI returns exact cost per call
}


def encode(img):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def call(model, b64):
    if model.startswith("claude"):
        c = anthropic.Anthropic(api_key=os.environ["AnthropicChatGraphPooledProdApiKey"], max_retries=4)
        r = c.messages.create(
            model=model, max_tokens=8000,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": PROMPT}]}],
        )
        text = "".join(b.text for b in r.content if b.type == "text")
        return text, r.usage.input_tokens, r.usage.output_tokens, None
    if model.startswith("grok"):
        c = openai.OpenAI(api_key=os.environ["XAI_CHATGRAPH_PROD_API_KEY"], base_url="https://api.x.ai/v1", max_retries=4)
    else:
        c = openai.OpenAI(api_key=os.environ["OpenAIChatGraphPooledProdApiKey"], max_retries=4)
    r = c.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
            {"type": "text", "text": PROMPT}]}],
    )
    ticks = getattr(r.usage, "cost_in_usd_ticks", None)
    cost = ticks / 1e10 if ticks is not None else None
    return r.choices[0].message.content, r.usage.prompt_tokens, r.usage.completion_tokens, cost


def parse(text):
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0))
    dets = []
    for o in data.get("objects", []):
        lab = o.get("label")
        b = o.get("box_2d")
        if lab not in CLASS_NAMES or not b or len(b) != 4:
            continue
        y0, x0, y1, x1 = [min(1.0, max(0.0, float(v) / 1000)) for v in b]
        if x1 <= x0 or y1 <= y0:
            continue
        dets.append({"label": lab, "score": float(o.get("confidence", 1.0) or 1.0), "box": [x0, y0, x1, y1]})
    present = {c: str(data.get("present", {}).get(c, "no")).lower() for c in CLASS_NAMES}
    return dets, present, data.get("ground_type")


def run_one(model, it):
    img, _ = load_image(it["path"])
    b64 = encode(img)
    t = time.time()
    last = None
    for attempt in range(3):
        try:
            text, tin, tout, cost = call(model, b64)
            dets, present, ground = parse(text)
            break
        except Exception as e:  # noqa: BLE001 - record and retry any API/parse failure
            last = repr(e)[:300]
            time.sleep(3 * (attempt + 1))
    else:
        return {"id": it["id"], "model": model, "error": last}
    pin, pout = PRICES.get(model, (None, None))
    if cost is None and pin is not None:
        cost = tin / 1e6 * pin + tout / 1e6 * pout
    return {"id": it["id"], "model": model, "seconds": time.time() - t, "size": [img.width, img.height],
            "tokens_in": tin, "tokens_out": tout, "cost_usd": cost,
            "dets": dets, "present": present, "ground_type": ground, "raw": text}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--images", default=str(B1 / "eval_images_batch1.json"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--ids", default="", help="comma-separated id prefixes to restrict to")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    items = [it for it in read_image_list(args.images) if Path(it["path"]).exists()]
    if args.ids:
        pre = args.ids.split(",")
        items = [it for it in items if any(it["id"].startswith(p) for p in pre)]
    if args.limit:
        items = items[: args.limit]
    out = Path(args.out) if args.out else B1 / f"results/vlm/{args.model}.jsonl"
    done = {r["id"]: r for r in read_jsonl(out)} if out.exists() else {}
    done = {k: v for k, v in done.items() if "error" not in v}
    todo = [it for it in items if it["id"] not in done]
    lock = threading.Lock()
    spent = [0.0]

    def work(it):
        r = run_one(args.model, it)
        with lock:
            done[it["id"]] = r
            spent[0] += r.get("cost_usd") or 0
            print(f"{len(done)} {it['id']} {len(r.get('dets', []))} dets ${spent[0]:.3f} {r.get('error', '')}", flush=True)
            if len(done) % 10 == 0:
                write_jsonl(out, list(done.values()))

    with ThreadPoolExecutor(args.workers) as ex:
        list(ex.map(work, todo))
    write_jsonl(out, list(done.values()))


if __name__ == "__main__":
    main()
