"""Screen extra panel candidates (Openverse hits) down to residential panel photos.

    python screen_panels.py /tmp/b2/openverse_extra_keep.json

Downloads each candidate to $B2_DATA/extra_cand/<n>.jpg and asks gpt-5.4-mini one question:
what equipment is shown. The question ignores legibility on purpose, so the kept set is not
pre-selected for readable labels. Writes $B2_DATA/extra_screen.jsonl.
"""

import base64
import io
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI
from PIL import Image, ImageOps

DATA = Path(os.environ.get("B2_DATA", "/tmp/b2data"))
UA = "house-scanning-b2-eval/0.1 (https://github.com/SamGu-NRX/house-scanning)"
QUESTION = """Classify what this photo mainly shows. Reply with one JSON object:
{"kind": one of "us_residential_breaker_panel", "residential_fuse_box", "meter_main_combo",
 "main_breaker_closeup", "panel_label_closeup", "loose_breakers", "non_us_consumer_unit",
 "commercial_or_industrial_panel", "not_electrical_panel", "other",
 "brand_hint": brand name if any is obvious, else null}"""


def fetch(item: tuple[int, dict]) -> dict:
    n, hit = item
    dest = DATA / "extra_cand" / f"c{n:04d}.jpg"
    if not dest.exists():
        try:
            request = urllib.request.Request(hit["url"], headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            with Image.open(io.BytesIO(data)) as image:
                ImageOps.exif_transpose(image).convert("RGB").save(dest, quality=92)
        except Exception as error:  # noqa: BLE001
            return {"n": n, "error": str(error)[:200], **hit}
    image = Image.open(dest)
    small = image.copy()
    small.thumbnail((512, 512))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=85)
    client = OpenAI(api_key=os.environ["OpenAIChatGraphPooledProdApiKey"])
    for attempt in range(3):
        try:
            r = client.chat.completions.create(
                model="gpt-5.4-mini",
                max_completion_tokens=300,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": QUESTION},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), "detail": "low"}},
                ]}],
            )
            answer = json.loads(r.choices[0].message.content)
            return {"n": n, "file": str(dest), "width": image.width, "height": image.height, **answer, **hit}
        except Exception as error:  # noqa: BLE001
            if attempt == 2:
                return {"n": n, "error": str(error)[:200], **hit}
            time.sleep(5)
    raise AssertionError("unreachable")


def main() -> None:
    hits = json.load(open(sys.argv[1]))
    (DATA / "extra_cand").mkdir(parents=True, exist_ok=True)
    out = DATA / "extra_screen.jsonl"
    with out.open("w") as handle, ThreadPoolExecutor(8) as pool:
        for record in pool.map(fetch, enumerate(hits)):
            handle.write(json.dumps(record) + "\n")
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    from collections import Counter

    print(Counter(r.get("kind", "ERR") for r in rows).most_common())


if __name__ == "__main__":
    main()
