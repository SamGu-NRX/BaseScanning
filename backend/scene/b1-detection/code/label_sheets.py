"""Pool every model's boxes per image into numbered candidates and render labelling sheets.

Candidates: gdino-tiny >= 0.3, owlv2 >= 0.2, gdino-base >= 0.3, and every VLM box; same-class
boxes with IoU >= 0.6 are merged (highest score kept, sources recorded). Writes
labels/candidates/<safe id>.json and labels/sheets/<n>.jpg (two images per sheet, with a 0.1 grid).

usage: python label_sheets.py --ids yt-,tt-,eth3d-,commons/ [--per-sheet 2]
"""

import argparse
import json

from PIL import Image

from common import B1, load_image, read_image_list, read_jsonl
from evaluate import iou
from render import draw_boxes

POOL = {"gdino-tiny": 0.3, "gdino-base": 0.3, "owlv2-base": 0.2}


def safe(i):
    return i.replace("/", "__")


def pooled(i, outs):
    cands = []
    for name, (rows, thr) in outs.items():
        r = rows.get(i)
        if not r:
            continue
        for d in r["dets"]:
            if d["score"] >= thr:
                cands.append({**d, "src": [name]})
    merged = []
    for d in sorted(cands, key=lambda d: -d["score"]):
        for m in merged:
            if m["label"] == d["label"] and iou(m["box"], d["box"]) >= 0.6:
                m["src"] = sorted(set(m["src"] + d["src"]))
                break
        else:
            merged.append(dict(d))
    for k, m in enumerate(merged):
        m["id"] = k
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="yt-,tt-,eth3d-,commons/")
    ap.add_argument("--per-sheet", type=int, default=2)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--hide", default="window,fence,driveway", help="classes not drawn (presence-only in labels)")
    args = ap.parse_args()
    outs = {}
    for p in list((B1 / "results/dets").glob("*.jsonl")) + list((B1 / "results/vlm").glob("*.jsonl")):
        thr = POOL.get(p.stem, 0.0 if p.parent.name == "vlm" else None)
        if thr is None:
            continue
        outs[p.stem] = ({r["id"]: r for r in read_jsonl(p) if "error" not in r}, thr)
    pre = args.ids.split(",")
    items = [it for it in read_image_list(B1 / "eval_images_batch1.json") if any(it["id"].startswith(p) for p in pre)]
    (B1 / "labels/candidates").mkdir(parents=True, exist_ok=True)
    (B1 / "labels/sheets").mkdir(parents=True, exist_ok=True)
    panels = []
    sheet = args.start
    index = []
    for it in items:
        try:
            img, _ = load_image(it["path"], 900)
        except FileNotFoundError:
            continue
        c = pooled(it["id"], outs)
        (B1 / f"labels/candidates/{safe(it['id'])}.json").write_text(json.dumps(c))
        shown = [d for d in c if d["label"] not in args.hide.split(",")]
        panels.append(draw_boxes(img, shown, numbered=True, grid=True, width=2))
        index.append(it["id"])
        if len(panels) == args.per_sheet:
            save(panels, sheet, index)
            sheet += 1
            panels, index = [], []
    if panels:
        save(panels, sheet, index)


def save(panels, n, index):
    W = max(p.width for p in panels)
    H = sum(p.height for p in panels) + 10 * (len(panels) - 1)
    s = Image.new("RGB", (W, H), (40, 40, 40))
    y = 0
    for p in panels:
        s.paste(p, (0, y))
        y += p.height + 10
    s.save(B1 / f"labels/sheets/{n:03d}.jpg", quality=85)
    print(n, index)


if __name__ == "__main__":
    main()
