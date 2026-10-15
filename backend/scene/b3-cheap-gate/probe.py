"""Linear probe on frozen CLIP / SigLIP image embeddings (cheap trained gate), grouped cross-validation.

    python probe.py [--feat /tmp/b3/feat]

Two heads, each a logistic regression on the L2-normalised embedding:
- reading: positives = 80 usable meter close-ups + 128 panel photos; negatives = walkaround frames.
- equipment (object tier): positives = walkaround / Commons frames where GPT-5.5 (all-frame run) says
  an equipment class is present (meter, panel, gas meter, AC, pool), plus the reading photos;
  negatives = frames where GPT-5.5 says none. GPT-5.5 answers are pseudo-labels (the baseline being
  imitated), not ground truth.
Folds: 5 groups; walkaround frames grouped by house, other photos assigned round-robin, so a house
is never in train and test at once. Threshold per fold = lowest held-in positive score quantile that
keeps 100% (and 98%) of the training positives. Reports held-out recall and the fraction of held-out
walkaround frames sent on. Writes results/probe_scores.json (held-out probability per frame).
"""

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

HERE = Path(__file__).resolve().parent
B2 = HERE.parent / "b2"
EQUIP = ["electric_meter", "main_panel", "gas_meter", "ac_condenser", "pool"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--feat", default="/tmp/b3/feat")
    args = p.parse_args()
    images = json.loads((HERE / "images.json").read_text())
    usable = {f"meter/{r['id']}" for r in csv.DictReader((B2 / "inputs/meter_manifest.csv").open()) if r["usable"] == "yes"}
    big = {}
    for x in Path("/tmp/b3/raw/presence/gpt-5.5.jsonl").read_text().splitlines():
        r = json.loads(x)
        if "error" not in r:
            big[r["id"]] = r
    houses = sorted({r["house"] for r in images if r["set"] == "walk"})
    group = {}
    k = 0
    for r in images:
        if r["set"] == "walk":
            group[r["id"]] = houses.index(r["house"]) % 5
        else:
            group[r["id"]] = k % 5
            k += 1
    out = {}
    for kind in ("clip", "siglip"):
        npz = np.load(Path(args.feat) / f"{kind}_emb.npz")
        X = {r["id"]: npz[r["id"].replace("/", "__")].astype(np.float32) for r in images if r["id"].replace("/", "__") in npz}
        heads = {}
        # reading head
        ids = [i for i in X if (i.startswith("meter/") and i in usable) or i.startswith(("panel/", "extra/")) or i.split("/")[0] in houses]
        y = {i: int(not i.split("/")[0] in houses) for i in ids}
        heads["reading"] = (ids, y)
        # equipment head (pseudo-labels from GPT-5.5 on walkaround + Commons; reading photos positive)
        ids2 = [i for i in X if i in big or (i.startswith("meter/") and i in usable) or i.startswith(("panel/", "extra/"))]
        y2 = {i: int(i not in big or any(big[i]["present"][c] == "yes" for c in EQUIP)) for i in ids2}
        heads["equipment"] = (ids2, y2)
        for head, (ids, y) in heads.items():
            prob = {}
            res = {"q100": [0, 0, 0, 0], "q98": [0, 0, 0, 0]}  # kept_pos, pos, sent_walk, walk
            for f in range(5):
                tr = [i for i in ids if group[i] != f]
                te = [i for i in ids if group[i] == f]
                clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000)
                clf.fit(np.stack([X[i] for i in tr]), [y[i] for i in tr])
                ptr = clf.predict_proba(np.stack([X[i] for i in tr]))[:, 1]
                pte = clf.predict_proba(np.stack([X[i] for i in te]))[:, 1]
                pos_tr = np.sort([s for s, i in zip(ptr, tr) if y[i]])
                for q, key in ((0.0, "q100"), (0.02, "q98")):
                    th = pos_tr[int(q * len(pos_tr))] * (0.5 if q == 0 else 1.0)
                    for s, i in zip(pte, te):
                        if y[i]:
                            res[key][1] += 1
                            res[key][0] += s >= th
                        if i.split("/")[0] in houses:
                            res[key][3] += 1
                            res[key][2] += s >= th
                prob.update({i: float(s) for s, i in zip(pte, te)})
            out[f"{kind}_{head}"] = prob
            for key, (kp, pn, ws, wn) in res.items():
                print(f"| {kind} probe | {head} | {key} | recall {kp}/{pn} ({kp / pn:.1%}) | walkaround frames sent {ws}/{wn} ({ws / wn:.0%}) |")
    (HERE / "results/probe_scores.json").write_text(json.dumps(out))


if __name__ == "__main__":
    main()
