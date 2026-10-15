"""Score every engine on the meter photos against the hand labels and PR #16's class labels.

    python score_meter.py            # prints markdown tables, writes results/meter_*.csv

Metrics, per engine:
- read: the labelled number appears in the engine's output (OCR: any line or same-row join, as in
  PR #16's `number_read`; VLM: in its meter_number or any listed candidate).
- top1 / top3 / ceiling: PR #16's ranking. OCR engines go through the vendored, unchanged
  `locate.candidates` + `ranked`; a VLM's list is its meter_number then number_candidates.
  A pick is right when its core equals the label's core (PR #16's rule).
- held-out: even-numbered photos only, the half PR #16 did not tune its ranking on.
- ansi class: CL number read on the 22 US-style plates; "false CL320" counts any CL320 claim.
"""

import csv
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "vendor"))
from meter_eval.locate import candidates, ranked  # noqa: E402
from meter_eval.match import core, normalize, normalize_class, rows_of_text  # noqa: E402
from meter_eval.stats import share  # noqa: E402

MANIFEST = HERE / "inputs" / "meter_manifest.csv"
LABELS = HERE / "labels" / "meter_numbers_hand.csv"
RAW = Path(os.environ.get("B2_RAW", HERE / "raw")) / "meter"
CL = re.compile(r"\bCL\s*[-.:]?\s*(\d{2,3})\b|\b(\d{2,3})\s*CL\b", re.IGNORECASE)
FIXUPS = str.maketrans({"O": "0", "I": "1", "L": "1"})

OCR_ENGINES = ["paddle", "doctr", "tesseract_psm11", "tesseract_psm3"]


def load(path: Path) -> dict[str, dict]:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                if "error" not in record:
                    out[record["id"]] = record
    return out


def class_number(text: str | None) -> str | None:
    m = CL.search(text or "")
    return f"CL{m.group(1) or m.group(2)}" if m else None


def ocr_view(result: dict) -> dict:
    texts = [t for t, _ in rows_of_text(result["lines"])]
    ranked_cores = ranked(candidates(result))
    cls = next((c for c in (class_number(t) for t in texts) if c), None)
    return {"texts": texts + [b["payload"] for b in result.get("barcodes", [])], "ranked": ranked_cores, "class": cls, "class_texts": texts}


def vlm_view(result: dict) -> dict:
    a = result.get("answer") or {}
    listed = [a.get("meter_number")] + list(a.get("number_candidates") or [])
    listed = [str(x) for x in listed if x]
    ranked_cores = list(dict.fromkeys(core(x) for x in listed if core(x)))
    label = a.get("class_label")
    return {
        "texts": listed,
        "ranked": ranked_cores,
        "class": class_number(label),
        "class_texts": [str(label)] if label else [],
        "answered": bool(a.get("meter_number")),
    }


def evaluate(engine: str, results: dict, manifest: dict, labels: dict) -> list[dict]:
    rows = []
    for image_id, m in manifest.items():
        if m["usable"] != "yes" or image_id not in results:
            continue
        view = vlm_view(results[image_id]) if engine.startswith("vlm_") else ocr_view(results[image_id])
        row = {"id": image_id, "engine": engine, "held_out": int(image_id[1:]) % 2 == 0,
               "us": m["class_kind"] == "ansi_class", "strict": m["number_agreed_strict"] == "yes"}
        label = labels.get(image_id)
        if label:
            norm = [normalize(t) for t in view["texts"]]
            row["read"] = any(label["number"] in t for t in norm)
            row["read_lenient"] = any(label["number"].translate(FIXUPS) in t.translate(FIXUPS) for t in norm)
            hits = [k for k, c in enumerate(view["ranked"]) if c == label["core"]]
            row["rank"] = hits[0] + 1 if hits else None
            row["top1_core"] = view["ranked"][0] if view["ranked"] else None
            others = {core(o) for o in label["other_identifiers"].split(";") if o}
            row["top1_other_id"] = bool(row["top1_core"]) and row["top1_core"] in others
            if engine.startswith("vlm_"):
                row["answered"] = view["answered"]
        if m["class_agreed"] == "yes" and m["class_label"]:
            if m["class_kind"] == "ansi_class":
                row["class_ok"] = view["class"] == class_number(m["class_label"])
            else:
                target = normalize_class(m["class_label"])
                row["class_ok"] = any(target in normalize_class(t) for t in view["class_texts"])
        row["claims_cl320"] = view["class"] == "CL320"
        rows.append(row)
    return rows


def summarize(engine: str, rows: list[dict]) -> dict:
    lab = [r for r in rows if "read" in r]
    held = [r for r in lab if r["held_out"]]
    us = [r for r in rows if r["us"] and "class_ok" in r]
    allc = [r for r in rows if "class_ok" in r]

    def topk(pool, k):
        return sum(1 for r in pool if r["rank"] and r["rank"] <= k)

    out = {
        "engine": engine,
        "n": len(lab),
        "read": share(sum(r["read"] for r in lab), len(lab)),
        "read_lenient": share(sum(r["read_lenient"] for r in lab), len(lab)),
        "top1": share(topk(lab, 1), len(lab)),
        "top3": share(topk(lab, 3), len(lab)),
        "ceiling": share(sum(1 for r in lab if r["rank"]), len(lab)),
        "top1_heldout": share(topk(held, 1), len(held)),
        "top3_heldout": share(topk(held, 3), len(held)),
        "top1_wrong_but_other_id": sum(1 for r in lab if r["rank"] != 1 and r["top1_other_id"]),
        "us_class": share(sum(r["class_ok"] for r in us), len(us)),
        "all_class": share(sum(r["class_ok"] for r in allc), len(allc)),
        "false_cl320": sum(r["claims_cl320"] for r in rows),
    }
    if engine.startswith("vlm_"):
        answered = [r for r in lab if r.get("answered")]
        out["answered"] = f"{len(answered)}/{len(lab)}"
        out["top1_precision"] = share(topk(answered, 1), len(answered))
    return out


def main() -> None:
    with MANIFEST.open() as h:
        manifest = {r["id"]: r for r in csv.DictReader(h)}
    with LABELS.open() as h:
        labels = {r["id"]: r for r in csv.DictReader(h)}
    engines = OCR_ENGINES + sorted(p.stem for p in RAW.glob("vlm_*.jsonl"))
    all_rows, summaries = [], []
    for engine in engines:
        results = load(RAW / f"{engine}.jsonl")
        if not results:
            continue
        rows = evaluate(engine, results, manifest, labels)
        all_rows += rows
        summaries.append(summarize(engine, rows))
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    keys = sorted({k for r in all_rows for k in r}, key=lambda k: (k not in ("id", "engine"), k))
    with (out_dir / "meter_per_image.csv").open("w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=keys, restval="")
        w.writeheader()
        w.writerows(all_rows)
    cols = ["engine", "n", "read", "top1", "top3", "ceiling", "top3_heldout", "us_class", "all_class", "false_cl320"]
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for s in summaries:
        print("| " + " | ".join(str(s.get(c, "")) for c in cols) + " |")
    print()
    for s in summaries:
        extra = {k: v for k, v in s.items() if k not in cols}
        print(s["engine"], extra)
    with (out_dir / "meter_summary.json").open("w") as h:
        json.dump(summaries, h, indent=1)


if __name__ == "__main__":
    main()
