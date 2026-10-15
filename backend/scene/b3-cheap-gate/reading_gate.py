"""Reading gate: which frames go to the meter / panel reading prompts?

    python reading_gate.py --scores results/gate_scores_all.json [results/gate_scores_sub.json]

Positives: the 80 usable meter close-ups (read_meter) and 128 panel photos (read_panel). Negatives:
walkaround frames (none of them shows a legible plate or label per house.json; any the gate sends
are wasted reading calls). Per gate, threshold = minimum score of the positives on the other half
(cross-fitted, as gate_eval.py). Also reports the text-detector count (DBNet) and sharpness
(Laplacian variance) as extra filters, and the gate's miss list.
"""

import argparse
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
B2 = HERE.parent / "b2"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scores", nargs="+", default=[str(HERE / "results/gate_scores_all.json")])
    p.add_argument("--feat", default="/tmp/b3/feat")
    args = p.parse_args()
    images = json.loads((HERE / "images.json").read_text())
    usable = {f"meter/{r['id']}" for r in csv.DictReader((B2 / "inputs/meter_manifest.csv").open()) if r["usable"] == "yes"}
    pos = {r["id"]: ("meter" if r["set"] == "meter" else "panel") for r in images
           if (r["set"] == "meter" and r["id"] in usable) or r["set"] in ("panel", "extra")}
    walk = [r["id"] for r in images if r["set"] == "walk"]
    gates = {}
    for s in args.scores:
        gates.update(json.loads(Path(s).read_text()))
    db = {}
    dbp = Path(args.feat) / "dbnet.jsonl"
    if dbp.exists():
        db = {json.loads(x)["id"]: json.loads(x)["n"] for x in dbp.read_text().splitlines() if x.strip()}
    if db:
        gates["dbnet_text"] = {i: {"meter": float(n), "panel": float(n)} for i, n in db.items()}
    print("| gate | reading recall (meter / panel) | walkaround frames sent to reading | walk frames scored |")
    print("|---|---|---|---|")
    out = {}
    for g, s in gates.items():
        ids = sorted(i for i in pos if i in s)
        folds = [ids[0::2], ids[1::2]]
        kept = {"meter": [0, 0], "panel": [0, 0]}
        ths = []
        for k in (0, 1):
            other = folds[1 - k]
            th = {c: min([s[i][c] for i in other if pos[i] == c] or [float("inf")]) for c in ("meter", "panel")}
            ths.append(th)
            for i in folds[k]:
                c = pos[i]
                kept[c][1] += 1
                kept[c][0] += s[i][c] >= th[c]
        th = {c: sum(t[c] for t in ths) / 2 for c in ("meter", "panel")}
        w = [i for i in walk if i in s]
        sent = sum(1 for i in w if s[i]["meter"] >= th["meter"] or s[i]["panel"] >= th["panel"])
        out[g] = {"meter": kept["meter"], "panel": kept["panel"], "walk_sent": sent, "walk": len(w), "th": th}
        print(f"| {g} | {kept['meter'][0]}/{kept['meter'][1]} / {kept['panel'][0]}/{kept['panel'][1]} | {sent} ({sent / max(1, len(w)):.0%}) | {len(w)} |")
    (HERE / "results/reading_gate.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
