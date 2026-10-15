"""Score detector and VLM outputs against labels/labels.jsonl.

Instance level: per class, greedy one-to-one matching by score at the pre-registered IoU
(common.MATCH_IOU), at the pre-registered default threshold per model and at the best-F1
threshold (tuned on the test set, so optimistic; reported separately).
Image level: presence per class (any detection above threshold; for VLMs their own yes/no answer).
Classes listed in a label row's `ignore` are skipped for that image.

usage: python evaluate.py  -> results/metrics.json and results/metrics.md
"""

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from common import B1, CLASS_NAMES, MATCH_IOU, read_image_list, read_jsonl

DEFAULT_THR = {"gdino-tiny": 0.35, "gdino-base": 0.35, "owlv2-base": 0.2, "florence2-base": 0.5}
VLM_THR = 0.0  # VLM boxes all count; they choose what to report
SETS = ["houses", "commons", "meters"]


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def nms_same_class(dets, thr=0.7):
    out = []
    for d in sorted(dets, key=lambda d: -d["score"]):
        if all(o["label"] != d["label"] or iou(o["box"], d["box"]) < thr for o in out):
            out.append(d)
    return out


def load_outputs():
    outs = {}
    for p in sorted((B1 / "results/dets").glob("*.jsonl")) + sorted((B1 / "results/vlm").glob("*.jsonl")):
        rows = {r["id"]: r for r in read_jsonl(p) if "error" not in r}
        outs[p.stem] = {"kind": "vlm" if p.parent.name == "vlm" else "det", "rows": rows}
    return outs


def match_counts(labels, rows, thr, ids):
    """Per-class (tp, fp, fn) and, for the sweep, per-class list of (score, is_tp) and n_gt."""
    tp, fp, fn = defaultdict(int), defaultdict(int), defaultdict(int)
    scored = defaultdict(list)
    ngt = defaultdict(int)
    for i in ids:
        lab, row = labels[i], rows[i]
        ign = set(lab.get("ignore", [])) | set(lab.get("instance_ignore", []))
        dets = nms_same_class(row["dets"])
        for c in CLASS_NAMES:
            if c in ign:
                continue
            gts = [o["box"] for o in lab["objects"] if o["label"] == c]
            ds = sorted([d for d in dets if d["label"] == c], key=lambda d: -d["score"])
            ngt[c] += len(gts)
            used = set()
            for d in ds:
                best, bj = 0.0, -1
                for j, g in enumerate(gts):
                    if j not in used:
                        v = iou(d["box"], g)
                        if v > best:
                            best, bj = v, j
                ok = best >= MATCH_IOU[c]
                if ok:
                    used.add(bj)
                scored[c].append((d["score"], ok))
                if d["score"] >= thr:
                    if ok:
                        tp[c] += 1
                    else:
                        fp[c] += 1
    for c in CLASS_NAMES:
        fn[c] = ngt[c] - tp[c]
    return tp, fp, fn, scored, ngt


def best_f1(scored, n_gt):
    if n_gt == 0 or not scored:
        return None
    s = sorted(scored, key=lambda x: -x[0])
    best = (0.0, None, 0.0, 0.0)
    tp = fp = 0
    for k, (score, ok) in enumerate(s):
        tp += ok
        fp += not ok
        if k + 1 < len(s) and s[k + 1][0] == score:
            continue
        p, r = tp / (tp + fp), tp / n_gt
        f = 2 * p * r / (p + r) if p + r else 0
        if f > best[0]:
            best = (f, score, p, r)
    return {"f1": best[0], "thr": best[1], "p": best[2], "r": best[3]}


def pr(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    return p, r


def presence(labels, rows, thr, ids, kind):
    """Per-class image-level tp/fp/fn/tn."""
    cnt = {c: [0, 0, 0, 0] for c in CLASS_NAMES}
    for i in ids:
        lab, row = labels[i], rows[i]
        ign = set(lab.get("ignore", []))
        for c in CLASS_NAMES:
            if c in ign:
                continue
            g = any(o["label"] == c for o in lab["objects"]) or lab.get("present_extra", {}).get(c, False)
            if kind == "vlm":
                pred = row["present"].get(c) == "yes"
            else:
                pred = any(d["label"] == c and d["score"] >= thr for d in row["dets"])
            k = (0 if pred else 2) if g else (1 if pred else 3)
            cnt[c][k] += 1
    return cnt


def fmt(x):
    return "–" if x is None else f"{x:.2f}"


def main():
    labels = {r["id"]: r for r in read_jsonl(B1 / "labels/labels.jsonl")}
    sets = {it["id"]: it["set"] for it in read_image_list(B1 / "eval_images_batch1.json")}
    outs = load_outputs()
    M = {}
    md = ["# B1 metrics (real images, agent-made labels)\n"]
    for name, o in outs.items():
        rows, kind = o["rows"], o["kind"]
        thr = VLM_THR if kind == "vlm" else DEFAULT_THR.get(name, 0.3)
        common_ids = [i for i in labels if i in rows]
        entry = {"kind": kind, "thr": thr, "n_images": len(common_ids), "by_set": {}}
        for sname in ["all"] + SETS:
            ids = [i for i in common_ids if sname == "all" or sets.get(i) == sname]
            if not ids:
                continue
            tp, fp, fn, scored, ngt = match_counts(labels, rows, thr, ids)
            pres = presence(labels, rows, thr, ids, kind)
            per = {}
            for c in CLASS_NAMES:
                p, r = pr(tp[c], fp[c], fn[c])
                pt, pf, pn, _ = pres[c]
                pp, prr = pr(pt, pf, pn)
                per[c] = {"n_gt": ngt[c], "tp": tp[c], "fp": fp[c], "fn": fn[c], "p": p, "r": r,
                          "best": best_f1(scored[c], ngt[c]),
                          "img": {"tp": pt, "fp": pf, "fn": pn, "tn": pres[c][3], "p": pp, "r": prr}}
            T = sum(tp.values()), sum(fp.values()), sum(fn.values())
            per["_micro"] = dict(zip(["p", "r"], pr(*T)))
            per["_micro"].update({"tp": T[0], "fp": T[1], "fn": T[2]})
            entry["by_set"][sname] = {"n": len(ids), "classes": per}
        # ground type
        if kind == "vlm":
            g = [(labels[i].get("ground_type"), rows[i].get("ground_type")) for i in common_ids
                 if labels[i].get("ground_type") and sets.get(i) != "meters"]
            entry["ground_acc"] = (sum(a == b for a, b in g) / len(g)) if g else None
            entry["ground_n"] = len(g)
        # confusions: gas meter predicted on images with an electric meter and no gas meter; water meter c022
        em_ids = [i for i in common_ids if (any(o["label"] == "electric_meter" for o in labels[i]["objects"])
                                            or labels[i].get("present_extra", {}).get("electric_meter"))
                  and not any(o["label"] == "gas_meter" for o in labels[i]["objects"])]
        def says_gas(i):
            r = rows[i]
            if kind == "vlm":
                return r["present"].get("gas_meter") == "yes"
            return any(d["label"] == "gas_meter" and d["score"] >= thr for d in r["dets"])
        entry["gas_on_electric"] = [sum(says_gas(i) for i in em_ids), len(em_ids)]
        entry["gas_on_water_meter_c022"] = says_gas("commons/c022") if "commons/c022" in common_ids else None
        # runtime / cost
        secs = [rows[i].get("seconds", 0) for i in common_ids]
        entry["sec_per_image"] = float(np.mean(secs)) if secs else None
        if kind == "vlm":
            costs = [rows[i].get("cost_usd") or 0 for i in common_ids]
            entry["usd_per_image"] = float(np.mean(costs)) if costs else None
            entry["usd_total_all_rows"] = float(sum((r.get("cost_usd") or 0) for r in rows.values()))
        M[name] = entry
    (B1 / "results/metrics.json").write_text(json.dumps(M, indent=1))

    # markdown tables
    names = list(M)
    for sname in ["all"] + SETS:
        md.append(f"\n## Instance P/R, set = {sname} (default thresholds; IoU per class as pre-registered)\n")
        md.append("| class | n_gt | " + " | ".join(names) + " |")
        md.append("|---|---|" + "---|" * len(names))
        for c in CLASS_NAMES + ["_micro"]:
            cells, n = [], None
            for nm in names:
                b = M[nm]["by_set"].get(sname)
                if not b:
                    cells.append("")
                    continue
                e = b["classes"][c]
                n = e.get("n_gt", n) if c != "_micro" else ""
                cells.append(f"{fmt(e['p'])} / {fmt(e['r'])}")
            md.append(f"| {c} | {n} | " + " | ".join(cells) + " |")
        md.append(f"\n## Image-level presence P/R, set = {sname}\n")
        md.append("| class | n_pos | " + " | ".join(names) + " |")
        md.append("|---|---|" + "---|" * len(names))
        for c in CLASS_NAMES:
            cells, npos = [], ""
            for nm in names:
                b = M[nm]["by_set"].get(sname)
                if not b:
                    cells.append("")
                    continue
                e = b["classes"][c]["img"]
                npos = e["tp"] + e["fn"]
                cells.append(f"{fmt(e['p'])} / {fmt(e['r'])}")
            md.append(f"| {c} | {npos} | " + " | ".join(cells) + " |")
    md.append("\n## Best-F1 instance thresholds (tuned on test: optimistic), set = all\n")
    md.append("| class | " + " | ".join(names) + " |")
    md.append("|---|" + "---|" * len(names))
    for c in CLASS_NAMES:
        cells = []
        for nm in names:
            b = M[nm]["by_set"]["all"]["classes"][c]["best"]
            cells.append("–" if not b or b["thr"] is None else f"F1 {b['f1']:.2f} (P {b['p']:.2f} R {b['r']:.2f} @ {b['thr']:.2f})")
        md.append(f"| {c} | " + " | ".join(cells) + " |")
    md.append("\n## Other\n")
    md.append("| model | images | s/image | $/image | ground-type acc | gas meter said on electric-meter images | gas on water meter c022 |")
    md.append("|---|---|---|---|---|---|---|")
    for nm in names:
        e = M[nm]
        md.append(f"| {nm} | {e['n_images']} | {fmt(e['sec_per_image'])} | {fmt(e.get('usd_per_image'))} | "
                  f"{fmt(e.get('ground_acc'))} (n={e.get('ground_n', '–')}) | {e['gas_on_electric'][0]}/{e['gas_on_electric'][1]} | {e['gas_on_water_meter_c022']} |")
    (B1 / "results/metrics.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
