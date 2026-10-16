"""Per-frame object presence with a VLM (OpenAI / xAI only), for the object-confirmation tier.

    python vlm_presence.py --model gpt-5.4-mini|gpt-5.5|grok-4.7 --sets walk,commons [--ids-file ids.txt] [--workers 8]

One call per frame, long side 1024 px (B1's setting), detail high. Presence only, no boxes (crops
come from the CPU stage). Writes $B3_RAW/presence/<model>.jsonl (default /tmp/b3/raw; copied into
results/raw/ when done).
"""

import argparse
import base64
import io
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import openai
from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
RAW = Path(os.environ.get("B3_RAW", "/tmp/b3/raw"))

CLASSES = {
    "electric_meter": "electric utility meter (round glass or digital meter on a socket, or a meter bank)",
    "main_panel": "electrical breaker panel / load center / deadfront / panel label (open or closed)",
    "gas_meter": "gas meter, gas regulator or gas shutoff valve assembly",
    "ac_condenser": "outdoor air-conditioner condenser or heat-pump unit",
    "water_heater": "water heater or tankless water heater",
    "pool": "swimming pool, or pool equipment (pump, filter, heater)",
    "hose_bib": "outdoor water faucet / hose bib",
    "downspout": "rain downspout",
    "vent": "wall or dryer vent",
    "comms_box": "small utility / comms / cable box on a wall",
    "window": "window",
    "door": "door or garage door",
}
PROMPT = """You are screening a photo for a home battery-installation site survey. For each class below say
whether at least one instance is visible: "yes", "no" or "unsure".
""" + "\n".join(f"- {k}: {v}" for k, v in CLASSES.items()) + """

Notes: a water meter is not a gas meter. A disconnect switch box alone is not a main_panel unless it is a
breaker panel. Only report what you can actually see.
Also report:
- "readable_equipment_text": "yes" if the photo shows equipment text (meter nameplate, panel label,
  breaker ratings) large and sharp enough to read, else "no".
- "quality": "good" | "blurry" | "dark" | "obstructed".
Reply with JSON only: {"present": {"<class>": "yes|no|unsure", ...}, "readable_equipment_text": "yes|no", "quality": "..."}"""

PRICES = {"gpt-5.5": (5.0, 30.0), "gpt-5.4-mini": (0.75, 4.5), "grok-4.20-0309-non-reasoning": (1.25, 2.5), "grok-4.7": (2.0, 6.0)}


def encode(path, max_side=1024):
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def call(model, b64):
    if model.startswith("grok"):
        c = openai.OpenAI(api_key=os.environ["XAI_CHATGRAPH_PROD_API_KEY"], base_url="https://api.x.ai/v1", max_retries=4)
    else:
        c = openai.OpenAI(api_key=os.environ["OpenAIChatGraphPooledProdApiKey"], max_retries=4)
    r = c.chat.completions.create(model=model, messages=[{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
        {"type": "text", "text": PROMPT}]}])
    return r.choices[0].message.content, r.usage.prompt_tokens, r.usage.completion_tokens


def run_one(model, row):
    b64 = encode(row["path"])
    last = None
    for attempt in range(3):
        t = time.time()
        try:
            text, tin, tout = call(model, b64)
            data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
            pin, pout = PRICES[model]
            return {"id": row["id"], "model": model, "secs": round(time.time() - t, 2), "tokens_in": tin, "tokens_out": tout,
                    "cost_usd": tin / 1e6 * pin + tout / 1e6 * pout,
                    "present": {k: str(data.get("present", {}).get(k, "no")).lower() for k in CLASSES},
                    "readable_equipment_text": data.get("readable_equipment_text"), "quality": data.get("quality")}
        except Exception as e:  # noqa: BLE001
            last = repr(e)[:300]
            time.sleep(3 * (attempt + 1))
    return {"id": row["id"], "model": model, "error": last}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--sets", default="walk,commons")
    p.add_argument("--ids-file", default="")
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args()
    rows = json.loads((HERE / "images.json").read_text())
    rows = [r for r in rows if r["set"] in set(args.sets.split(","))]
    if args.ids_file:
        keep = set(Path(args.ids_file).read_text().split())
        rows = [r for r in rows if r["id"] in keep]
    out = RAW / "presence" / f"{args.model}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(line)["id"] for line in out.read_text().splitlines() if line.strip() and '"error"' not in line} if out.exists() else set()
    todo = [r for r in rows if r["id"] not in done]
    print(f"{args.model}: {len(todo)} frames", flush=True)
    with out.open("a") as h, ThreadPoolExecutor(args.workers) as pool:
        for k, rec in enumerate(pool.map(lambda r: run_one(args.model, r), todo)):
            h.write(json.dumps(rec) + "\n")
            h.flush()
            if k % 50 == 0 or "error" in rec:
                print(k, rec["id"], rec.get("secs"), rec.get("error", ""), flush=True)


if __name__ == "__main__":
    main()
