"""Crop boxes for the reading tier, from the CPU stage -> results/crops_<method>.json.

    python crop.py [--feat /tmp/b3/feat]

owl:  best OWLv2 box for the equipment (meter: "electric meter" / "meter nameplate with printed text";
      panel: "electrical breaker panel" / "circuit breakers" / "electrical panel label"), score >= 0.1,
      padded 15% per side. Falls back to the full frame when no box qualifies or the box already
      covers >= 70% of the frame (the photo is already a close-up).
text: bounding box of DBNet text boxes that lie inside the owl box (or the whole frame), padded 10%;
      falls back to the owl crop when fewer than 3 text boxes are found.
Boxes are normalised [x0, y0, x1, y1] of the upright image.
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
QUERIES = {"meter": ["electric meter", "meter nameplate with printed text"],
           "panel": ["electrical breaker panel", "circuit breakers", "electrical panel label"]}


def pad(b, f):
    w, h = b[2] - b[0], b[3] - b[1]
    return [max(0.0, b[0] - f * w), max(0.0, b[1] - f * h), min(1.0, b[2] + f * w), min(1.0, b[3] + f * h)]


def area(b):
    return (b[2] - b[0]) * (b[3] - b[1])


def owl_box(dets, kind):
    cands = [d for q in QUERIES[kind] for d in dets.get(q, []) if d[0] >= 0.1]
    if not cands:
        return None
    best = max(cands)
    # Merge boxes of the same kind that overlap the best one (meter glass + nameplate, panel + label).
    b = best[1:]
    for d in cands:
        x = d[1:]
        inter = max(0, min(b[2], x[2]) - max(b[0], x[0])) * max(0, min(b[3], x[3]) - max(b[1], x[1]))
        if inter > 0.3 * area(x) and d[0] >= 0.5 * best[0]:
            b = [min(b[0], x[0]), min(b[1], x[1]), max(b[2], x[2]), max(b[3], x[3])]
    b = pad(b, 0.15)
    return None if area(b) >= 0.7 else b


def enclosure_box(dets, kind):
    """v2: whole meter / whole panel enclosure only (no nameplate or single-breaker boxes), padded 25%;
    full frame unless the enclosure is under 50% of the frame."""
    q = "electric meter" if kind == "meter" else "electrical breaker panel"
    cands = [d for d in dets.get(q, []) if d[0] >= 0.1]
    if not cands:
        return None
    b = pad(max(cands)[1:], 0.25)
    return None if area(max(cands)[1:]) >= 0.5 or area(b) >= 0.8 else b


def text_box(words, within):
    lo = within or [0, 0, 1, 1]
    inside = [w for w in words if w[0] >= lo[0] and w[1] >= lo[1] and w[2] <= lo[2] and w[3] <= lo[3]]
    if len(inside) < 3:
        return within
    b = [min(w[0] for w in inside), min(w[1] for w in inside), max(w[2] for w in inside), max(w[3] for w in inside)]
    b = pad(b, 0.10)
    return None if area(b) >= 0.7 else b


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--feat", default="/tmp/b3/feat")
    args = p.parse_args()
    owl = {json.loads(x)["id"]: json.loads(x) for x in (Path(args.feat) / "owlv2.jsonl").read_text().splitlines() if x.strip()}
    dbp = Path(args.feat) / "dbnet.jsonl"
    db = {json.loads(x)["id"]: json.loads(x) for x in dbp.read_text().splitlines() if x.strip()} if dbp.exists() else {}
    out = {"owl": {}, "text": {}, "enclosure": {}}
    for i, r in owl.items():
        s = i.split("/")[0]
        kind = "meter" if s == "meter" else "panel" if s in ("panel", "extra") else None
        if not kind:
            continue
        b = owl_box(r["dets"], kind)
        out["owl"][i] = b
        out["enclosure"][i] = enclosure_box(r["dets"], kind)
        if i in db:
            out["text"][i] = text_box(db[i]["boxes"], b)
    for m, boxes in out.items():
        full = sum(1 for b in boxes.values() if b is None)
        mean = sum(area(b) for b in boxes.values() if b) / max(1, len(boxes) - full)
        print(f"{m}: {len(boxes)} photos, {full} kept full-frame, mean crop area {mean:.0%} of frame")
        (HERE / "results" / f"crops_{m}.json").write_text(json.dumps(boxes, indent=0))


if __name__ == "__main__":
    main()
