"""eth3d-facade has no real meter, so photo-only runs can never find one. For a fair comparison with
the tool variant, both variants get the same packet with the invented meter drawn as a red cross
wherever the laser scan says it is visible, plus one sentence saying so.

python3 meter_hint.py [--models claude-sonnet-5,claude-opus-5-5] [--prompts p06,p07,p08,p12]
Results: results/eth3d-facade/photo_only_meter_hint_verbatim/<model>/<prompt_id>.json
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import geometry as G
import ssr_prompts as S
import vlm

HOUSE = "eth3d-facade"
NOTE = ("Note: the electric meter itself is not visible in these photos. Its location on the wall is marked with a red cross "
        "labelled \"electric meter\" in each photo where that spot is visible (about 5 ft above the ground). Treat that spot as the meter.\n\n")
CACHE = Path.home() / "cache/c3" / HOUSE / "meter_hint"


def marked_packet(geo: G.Geometry) -> list[vlm.Photo]:
    photos = vlm.build_packet(HOUSE)
    tools = G.SceneTools(geo, photos)
    X = geo.anchors["electric_meter"]
    CACHE.mkdir(parents=True, exist_ok=True)
    out, seen = [], []
    for ph in photos:
        cam = geo.cameras[ph.frame]
        Xc = cam.R @ X + cam.t
        img = Image.open(ph.path).convert("RGB")
        if Xc[2] > 0.1:
            u = (cam.K[0, 0] * Xc[0] / Xc[2] + cam.K[0, 2]) * ph.scale
            v = (cam.K[1, 1] * Xc[1] / Xc[2] + cam.K[1, 2]) * ph.scale
            if 0 <= u < ph.width and 0 <= v < ph.height:
                depth, _ = tools._depth_buffer(ph.index)  # half packet resolution
                vi, ui = int(v * 0.5), int(u * 0.5)
                win = depth[max(0, vi - 3):vi + 4, max(0, ui - 3):ui + 4]
                win = win[np.isfinite(win)]
                d = float(np.median(win)) if win.size else np.inf
                if not np.isfinite(d) or d > Xc[2] - 0.4:
                    dr = ImageDraw.Draw(img)
                    r = max(10, ph.width // 60)
                    dr.line([u - r, v, u + r, v], fill=(255, 0, 0), width=4)
                    dr.line([u, v - r, u, v + r], fill=(255, 0, 0), width=4)
                    dr.text((u + r // 2, v + r // 2), "electric meter", fill=(255, 0, 0))
                    seen.append(ph.index)
        p = CACHE / ph.path.name
        img.save(p, quality=90)
        out.append(vlm.Photo(ph.index, ph.frame, p, ph.width, ph.height, ph.scale))
    print("meter cross drawn in photos", seen, flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="claude-sonnet-5,claude-opus-5-5,gpt-5.5")
    ap.add_argument("--prompts", default="p06,p07,p08,p12")
    a = ap.parse_args()
    photos = marked_packet(G.load_eth3d_facade())
    prompts = [p for p in S.PROMPTS if any(p.id.startswith(w) for w in a.prompts.split(","))]
    variant = "photo_only_meter_hint_verbatim"

    def job(model, prompt):
        out = vlm.RESULTS / HOUSE / variant / model / f"{prompt.id}.json"
        if out.exists():
            return
        r = vlm.call_photo_prompt(model, "", photos, NOTE + prompt.text, prompt_first=True)
        r.update(prompt_id=prompt.id, house=HOUSE, variant=variant, verdicts=S.normalize(prompt.id, r["json"]) if r.get("json") else {})
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(r, indent=1))
        print(model, prompt.id, r["verdicts"], f"${r['cost_usd']:.3f}", flush=True)

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda mp: job(*mp), [(m, p) for m in a.models.split(",") for p in prompts]))


if __name__ == "__main__":
    main()
