"""Can a VLM name the meter's make, model family and cover type from the close-up (the input a
datasheet lookup needs)? Runs on B2's 22 ANSI socket-meter photos (real, Wikimedia Commons).

    python meter_model.py --model gpt-5.5   ->  results/meter_model_<model>.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
B2 = HERE.parent / "b2"
sys.path.insert(0, str(B2))
import vlm  # noqa: E402

IMG = Path.home() / "a9/meter_images"
IDS = ("m01 m03 m09 m11 m13 m15 m22 m23 m27 m28 m29 m31 m32 m34 m35 m37 m39 m44 m45 m50 m51 m57").split()

PROMPT = """This is a close-up photo of an electricity meter. Identify the physical meter model so its
datasheet dimensions can be looked up. Return one JSON object and nothing else, with keys:
  manufacturer: brand printed or clearly recognisable, else null.
  model_family: product family/model (e.g. "Itron CENTRON", "Itron OpenWay CENTRON", "Landis+Gyr Focus AX",
    "Aclara I-210+", "GE I-210", "GE kV2c", "Elster REX2", "Elster A3 Alpha", "Sensus iConA",
    "Westinghouse D4S"), else null.
  model_evidence: "printed" if the model name/type code is legible on the plate, "visual" if inferred
    from the housing look, null if unknown.
  model_confidence: 0-1.
  cover_material: "glass", "polycarbonate" or "unknown".
  ansi_socket_meter: true if it is a round North-American socket (S-base) meter, false otherwise.
  base_flange_visible: true if the meter's base rim/flange (the widest ring where the meter meets the
    socket, often under a sealing ring) is visible around most of its circumference.
  socket_type: "ring" (separate sealing ring around the base), "ringless" (socket cover clamps the rim),
    or "unknown".
"""


def fetch():
    IMG.mkdir(parents=True, exist_ok=True)
    rows = {r["id"]: r for r in csv.DictReader(open(B2 / "inputs/meter_manifest.csv"))}
    for i in IDS:
        p = IMG / f"{i}.jpg"
        if p.exists():
            continue
        name = urllib.parse.quote(rows[i]["title"].removeprefix("File:"))
        url = f"https://commons.wikimedia.org/w/index.php?title=Special:Redirect/file/{name}&width=2048"
        for attempt in range(6):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "a9-research/0.1 (internal eval; contact via store)"})
                p.write_bytes(urllib.request.urlopen(req, timeout=60).read())
                break
            except urllib.error.HTTPError:
                time.sleep(10 * (attempt + 1))
        time.sleep(3)


def one(model, i):
    b64 = vlm.encode(IMG / f"{i}.jpg")
    text, usage = vlm.call(model, PROMPT, b64)
    return {"id": i, "model": model, "usage": usage, "answer": vlm.parse(text), "raw": text[:400]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-5.5")
    a = ap.parse_args()
    fetch()
    out = HERE / "results" / f"meter_model_{a.model}.jsonl"
    with open(out, "w") as f, ThreadPoolExecutor(6) as ex:
        for r in ex.map(lambda i: one(a.model, i), IDS):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(r["id"], json.dumps(r["answer"], ensure_ascii=False)[:200], flush=True)


if __name__ == "__main__":
    main()
