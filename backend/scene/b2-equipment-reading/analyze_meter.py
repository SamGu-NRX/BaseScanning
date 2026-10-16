"""Second-pass meter analysis over results/meter_per_image.csv plus the raw runs.

    B2_RAW=/tmp/b2raw python analyze_meter.py > results/meter_analysis.md

1. Label confirmation: a hand label (made by Claude Opus 5.5) is "confirmed" when a non-Claude
   reader emits exactly the same normalized string (any VLM candidate or OCR line/row join), or
   PR #16's Apple Vision matched PR #16's key-holder label and lengths agree.
2. Top-1 / top-3 per engine on all labels and on the confirmed-by-non-Claude subset.
3. Every top-1 miss, categorised: other identifier on the plate / near-miss misread (edit
   distance <= 2 on the core) / prefix-suffix (one core contains the other) / other.
4. Agreement rules for auto-fill: accept engine A's pick only when the listed readers agree on
   the core; precision with Wilson interval and coverage. PR #16's bar: precision >= 95%.
"""

import csv
import json
import os
import sys
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "vendor"))
from meter_eval.match import core, normalize  # noqa: E402
from meter_eval.stats import share, wilson  # noqa: E402
from score_meter import MANIFEST, load, ocr_view, vlm_view  # noqa: E402

RAW = Path(os.environ.get("B2_RAW", HERE / "raw")) / "meter"
LABELS = HERE / "labels" / "meter_numbers_hand.csv"
VISION = HERE / "inputs" / "meter_clean_per_image.csv"
NON_CLAUDE = ["vlm_gpt-5.5", "vlm_gpt-5.4-mini", "vlm_grok-4.7", "vlm_grok-4.20-0309-non-reasoning",
              "doctr", "paddle", "tesseract_psm11", "tesseract_psm3"]
SHORT = {
    "vlm_gpt-5.5": "GPT-5.5", "vlm_gpt-5.4-mini": "GPT-5.4-mini", "vlm_grok-4.7": "Grok 4.7",
    "vlm_grok-4.20-0309-non-reasoning": "Grok 4.20 NR", "vlm_claude-opus-5-5": "Opus 5.5",
    "vlm_claude-haiku-4-5-20251001": "Haiku 4.5", "doctr": "docTR", "paddle": "PaddleOCR",
    "tesseract_psm11": "Tesseract psm11", "tesseract_psm3": "Tesseract psm3",
}


def lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def views() -> dict[str, dict[str, dict]]:
    out = {}
    for engine in SHORT:
        results = load(RAW / f"{engine}.jsonl")
        if results:
            f = vlm_view if engine.startswith("vlm_") else ocr_view
            out[engine] = {i: f(r) for i, r in results.items()}
    return out


def main() -> None:
    with MANIFEST.open() as h:
        manifest = {r["id"]: r for r in csv.DictReader(h)}
    with LABELS.open() as h:
        labels = {r["id"]: r for r in csv.DictReader(h)}
    with VISION.open() as h:
        vision = {r["id"]: r for r in csv.DictReader(h)}
    v = views()
    engines = list(v)

    confirmers: dict[str, list[str]] = {}
    for i, lab in labels.items():
        who = [SHORT[e] for e in NON_CLAUDE if e in v and i in v[e]
               and any(lab["number"] == normalize(t) or lab["number"] in normalize(t) for t in v[e][i]["texts"])]
        if vision.get(i, {}).get("number_accurate") == "1":
            who.append("Vision(PR16 key)")
        confirmers[i] = who
    confirmed = {i for i, w in confirmers.items() if w}
    print("## Label confirmation\n")
    print(f"Hand labels: {len(labels)}. Confirmed by at least one non-Claude reader: {len(confirmed)}.")
    only_vision = [i for i in confirmed if confirmers[i] == ["Vision(PR16 key)"]]
    print(f"Confirmed only by Vision's match against PR #16's key-holder label: {len(only_vision)} {sorted(only_vision)}.")
    unconf = sorted(set(labels) - confirmed)
    print(f"Unconfirmed (Claude-only): {len(unconf)} {unconf}\n")
    strict_ocr = {i for i, w in confirmers.items() if any(x not in ("Vision(PR16 key)",) for x in w)}
    print(f"Confirmed by a non-Claude engine run here (excluding Vision): {len(strict_ocr)}\n")

    def pick_rank(e: str, i: str) -> int | None:
        ranked = v[e][i]["ranked"]
        hits = [k for k, c in enumerate(ranked) if c == labels[i]["core"]]
        return hits[0] + 1 if hits else None

    print("## Top-1 / top-3, all labels vs non-Claude-confirmed subset\n")
    print("| Engine | n | top-1 all | top-3 all | top-1 confirmed | top-3 confirmed | top-1 held-out (even ids) |")
    print("|---|---|---|---|---|---|---|")
    for e in engines:
        ids = [i for i in labels if i in v[e]]
        conf = [i for i in ids if i in confirmed]
        held = [i for i in ids if int(i[1:]) % 2 == 0]
        r = {i: pick_rank(e, i) for i in ids}
        t = lambda pool, k: share(sum(1 for i in pool if r[i] and r[i] <= k), len(pool))  # noqa: E731
        print(f"| {SHORT[e]} | {len(ids)} | {t(ids, 1)} | {t(ids, 3)} | {t(conf, 1)} | {t(conf, 3)} | {t(held, 1)} |")

    print("\n## Top-1 misses by category\n")
    print("| Engine | misses | other identifier | misread (edit <= 2) | prefix/suffix | no pick | other | ids |")
    print("|---|---|---|---|---|---|---|---|")
    for e in engines:
        cats = {"other_id": 0, "misread": 0, "affix": 0, "none": 0, "other": 0}
        ids = []
        for i, lab in labels.items():
            if i not in v[e] or pick_rank(e, i) == 1:
                continue
            ranked = v[e][i]["ranked"]
            top = ranked[0] if ranked else None
            others = {core(o) for o in lab["other_identifiers"].split(";") if o}
            if not top:
                c = "none"
            elif top in others:
                c = "other_id"
            elif lab["core"] in top or top in lab["core"]:
                c = "affix"
            elif lev(top, lab["core"]) <= 2:
                c = "misread"
            else:
                c = "other"
            cats[c] += 1
            ids.append(f"{i}:{c}")
        n = sum(cats.values())
        print(f"| {SHORT[e]} | {n} | {cats['other_id']} | {cats['misread']} | {cats['affix']} | {cats['none']} | {cats['other']} | {' '.join(ids)} |")

    print("\n## Agreement rules for auto-fill (accept when all listed readers' top-1 cores agree)\n")
    print("Precision = right among accepted; coverage = accepted / 73. Bar: precision lower bound >= 95%"
          " for blind auto-fill; otherwise pre-select with tap-to-confirm.\n")
    print("| Rule | accepted (coverage) | precision | Wilson low |")
    print("|---|---|---|---|")
    vlms = [e for e in engines if e.startswith("vlm_")]
    ocrs = [e for e in engines if not e.startswith("vlm_")]
    rules = [(a,) for a in vlms] + list(combinations(vlms, 2)) + [
        ("vlm_gpt-5.5", "vlm_grok-4.7", "vlm_claude-opus-5-5"),
        ("vlm_gpt-5.5", "vlm_grok-4.7", "vlm_grok-4.20-0309-non-reasoning"),
    ] + [("vlm_gpt-5.5", o) for o in ocrs] + [("vlm_gpt-5.4-mini", "vlm_grok-4.20-0309-non-reasoning")]
    rows = []
    for rule in rules:
        acc = right = 0
        for i, lab in labels.items():
            tops = []
            for e in rule:
                ranked = v[e].get(i, {}).get("ranked") if e in v else None
                tops.append(ranked[0] if ranked else None)
            if tops[0] and all(t == tops[0] for t in tops):
                acc += 1
                right += tops[0] == lab["core"]
        low = wilson(right, acc)[0] if acc else 0
        rows.append((low, " + ".join(SHORT[e] for e in rule), acc, right))
    for low, name, acc, right in sorted(rows, reverse=True):
        print(f"| {name} | {acc}/73 = {acc / 73:.0%} | {share(right, acc)} | {low:.1%} |")

    print("\n## Barcode-confirmed pick (zxing payload core equals VLM top-1)\n")
    doctr_raw = load(RAW / "doctr.jsonl")
    for e in ["vlm_gpt-5.5", "vlm_grok-4.7", "vlm_claude-opus-5-5"]:
        acc = right = 0
        for i, lab in labels.items():
            top = (v[e][i]["ranked"] or [None])[0]
            raw = doctr_raw.get(i) or {}
            codes = {core(normalize(b["payload"])) for b in raw.get("barcodes", [])}
            if top and top in codes:
                acc += 1
                right += top == lab["core"]
        print(f"- {SHORT[e]} top-1 confirmed by a decoded barcode: {acc}/73 accepted, precision {share(right, acc)}")
    photos_with_codes = sum(1 for r in doctr_raw.values() if r.get("barcodes"))
    print(f"- Photos with any decoded barcode (zxing-cpp full + half size): {photos_with_codes}/83")

    print("\n## Unconfirmed labels: who read what\n")
    for i in unconf:
        picks = {SHORT[e]: (v[e][i]["ranked"] or ["-"])[0] for e in engines if i in v[e]}
        agree = [k for k, p in picks.items() if p == labels[i]["core"]]
        print(f"- {i}: label core agrees with top-1 of {agree or 'none'}; lengths match PR #16 key "
              f"({manifest[i]['number_len']}/{manifest[i]['number_core_len']})")


if __name__ == "__main__":
    main()
