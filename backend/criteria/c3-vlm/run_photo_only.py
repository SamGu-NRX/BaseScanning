"""Photo-only baseline: run each of the 13 SSR prompts, as one call each, on a house's photo packet.

Usage: python run_photo_only.py [--houses a,b] [--models m1,m2] [--prompts p01,p04]
Prompts: verbatim SSR_Prompts.md (ssr_prompts.py), prompt text first, then the photos.
Results: results/<house>/photo_only_verbatim/<model>/<prompt_id>.json (skipped when already present).
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import ssr_prompts as S
import vlm

DEFAULT_MODELS = ["claude-sonnet-5", "gpt-5.5", "claude-opus-5-5"]


def run_one(house: str, model: str, photos: list[vlm.Photo], prompt: S.SSRPrompt, variant: str = "photo_only_verbatim") -> dict:
    out_path = vlm.RESULTS / house / variant / model / f"{prompt.id}.json"
    if out_path.exists():
        return json.loads(out_path.read_text())
    for attempt in range(2):
        try:
            r = vlm.call_photo_prompt(model, S.PREAMBLE, photos, prompt.text, prompt_first=True)
        except Exception as e:  # provider errors are recorded, not fatal
            r = {"model": model, "error": repr(e)[:1000], "json": None, "cost_usd": 0.0, "usage": {}}
        if r.get("json") is not None:
            break
    r["prompt_id"] = prompt.id
    r["house"] = house
    r["variant"] = variant
    r["verdicts"] = S.normalize(prompt.id, r["json"]) if r.get("json") else {}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(r, indent=1))
    return r


def run_house_model(house: str, model: str, prompts: list[S.SSRPrompt]) -> None:
    photos = vlm.build_packet(house)
    pk = vlm.RESULTS / house / "packet.json"
    pk.parent.mkdir(parents=True, exist_ok=True)
    pk.write_text(json.dumps([{"photo": p.index, "frame": p.frame, "w": p.width, "h": p.height, "scale": p.scale} for p in photos], indent=1))
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda p: run_one(house, model, photos, p), prompts))
    total = sum(json.loads((vlm.RESULTS / house / "photo_only_verbatim" / model / f"{p.id}.json").read_text()).get("cost_usd", 0) for p in prompts)
    print(f"{house} {model}: {len(prompts)} prompts, ${total:.3f}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--houses", default="")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--prompts", default="")
    a = ap.parse_args()
    houses = [h for h in a.houses.split(",") if h] or vlm.ready_houses()
    models = [m for m in a.models.split(",") if m]
    want = [p for p in a.prompts.split(",") if p]
    prompts = [p for p in S.PROMPTS if not want or any(p.id.startswith(w) for w in want)]
    print("houses:", houses, "models:", models, "prompts:", len(prompts), flush=True)
    for h in houses:
        vlm.build_packet(h)
    jobs = [(h, m) for h in houses for m in models]
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(run_house_model, h, m, prompts) for h, m in jobs]
        for f in futs:
            try:
                f.result()
            except Exception as e:
                print("job failed:", repr(e)[:500], file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
