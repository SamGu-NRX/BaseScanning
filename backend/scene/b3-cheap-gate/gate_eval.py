"""Score the CPU gates: recall of must-keep frames vs fraction of walkaround frames filtered out.

    python gate_eval.py [--feat /tmp/b3/feat]

Per gate, each frame gets a score per equipment class (meter, panel, gas_meter, ac_condenser, pool,
water_heater). A frame is kept if any class score reaches that class's threshold. Thresholds are
cross-fitted: labelled frames are split in two halves (alternating by id); each half's thresholds are
the minimum positive score of that class on the *other* half (i.e. tuned to keep 100% there), times a
safety margin. Reported recall is pooled over both held-out halves; the filtered fraction is over all
1,321 walkaround frames (thresholds averaged across folds) and over the labelled negatives.
Writes results/gate_scores.json (per-frame class scores per gate) and results/gate_eval.json.
"""

import argparse
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CLASSES = ["meter", "panel", "gas_meter", "ac_condenser", "pool", "water_heater"]
MUST = ["meter", "panel", "gas_meter", "ac_condenser", "pool"]

PROMPTS = {
    "meter": ["a close-up photo of an electric meter", "an electricity meter on a house wall", "a meter nameplate with printed numbers"],
    "panel": ["an electrical breaker panel", "a circuit breaker box with breakers", "an electrical panel label", "a fuse box"],
    "gas_meter": ["a gas meter with pipes", "a gas regulator and gas meter beside a house"],
    "ac_condenser": ["an outdoor air conditioner condenser unit", "a heat pump unit beside a house"],
    "pool": ["a swimming pool"],
    "water_heater": ["a water heater tank"],
    "downspout": ["a rain downspout on a house wall"],
    "hose_bib": ["an outdoor water faucet on a wall"],
    "vent": ["a dryer vent on an exterior wall"],
    "comms_box": ["a small grey utility box on a wall", "a cable tv box on a house"],
    "window": ["a window of a house"],
    "door": ["a door of a house", "a garage door"],
    "other": ["a lawn", "the sky", "trees", "a street", "a roof", "the exterior of a house", "a brick wall", "a fence",
              "a driveway", "a car", "a room interior", "a person"],
}
OWL_MAP = {
    "meter": ["electric meter", "meter nameplate with printed text"],
    "panel": ["electrical breaker panel", "electrical panel label", "circuit breakers"],
    "gas_meter": ["gas meter"], "ac_condenser": ["air conditioner condenser unit"], "pool": ["swimming pool"],
    "water_heater": ["water heater"],
}


def read(path):
    return {r["id"]: r for r in (json.loads(x) for x in Path(path).read_text().splitlines() if x.strip())}


def text_emb(kind, prompts):
    import torch

    if kind.startswith("clip"):
        import open_clip

        arch, tag = ("ViT-B-32", "laion2b_s34b_b79k") if kind == "clip" else ("ViT-L-14", "datacomp_xl_s13b_b90k")
        model, _, _ = open_clip.create_model_and_transforms(arch, pretrained=tag)
        tok = open_clip.get_tokenizer(arch)
        with torch.no_grad():
            e = model.encode_text(tok(prompts))
        scale, bias = float(model.logit_scale.exp()), 0.0
    else:
        from transformers import AutoModel, AutoProcessor

        name = "google/siglip2-base-patch16-256"
        model = AutoModel.from_pretrained(name).eval()
        proc = AutoProcessor.from_pretrained(name)
        with torch.no_grad():
            inp = proc(text=prompts, padding="max_length", max_length=64, return_tensors="pt")
            e = model.get_text_features(**inp)
            if not isinstance(e, torch.Tensor):
                e = e.pooler_output
        scale, bias = float(model.logit_scale.exp()), float(model.logit_bias)
    e = e / e.norm(dim=-1, keepdim=True)
    return e.numpy(), scale, bias


def embedding_scores(kind, feat_dir, ids):
    npz = np.load(Path(feat_dir) / f"{kind}_emb.npz")
    prompts, owner = [], []
    for c, ps in PROMPTS.items():
        prompts += ps
        owner += [c] * len(ps)
    t, scale, bias = text_emb(kind, prompts)
    owner = np.array(owner)
    out = {}
    for i in ids:
        key = i.replace("/", "__")
        if key not in npz:
            continue
        sims = npz[key].astype(np.float32) @ t.T
        if kind.startswith("clip"):
            p = np.exp(scale * sims - (scale * sims).max())
            p /= p.sum()
            out[i] = {c: float(p[owner == c].sum()) for c in CLASSES}
        else:
            p = 1 / (1 + np.exp(-(scale * sims + bias)))
            out[i] = {c: float(p[owner == c].max()) for c in CLASSES}
    return out


def owl_scores(feat_dir):
    rows = read(Path(feat_dir) / "owlv2.jsonl")
    return {i: {c: max(r["max"].get(q, 0.0) for q in OWL_MAP[c]) for c in CLASSES} for i, r in rows.items()}


def text_scores(feat_dir):
    rows = read(Path(feat_dir) / "dbnet.jsonl")
    # One "text" score reused for every class: the gate keeps text-dense frames.
    return {i: {c: float(r["n"]) for c in CLASSES} for i, r in rows.items()}


def evaluate(scores, gt, images, margin=1.0):
    lab = sorted(i for i in scores if gt[i]["labelled"])
    folds = [lab[0::2], lab[1::2]]
    walk = [r["id"] for r in images if r["set"] == "walk" and r["id"] in scores]

    def thresholds(pool):
        th = {}
        for c in MUST:
            pos = [scores[i][c] for i in pool if c in gt[i]["classes"]]
            th[c] = min(pos) * margin if pos else float("inf")
        return th

    def keep(i, th):
        return any(scores[i][c] >= th[c] for c in MUST)

    kept_pos = tot_pos = kept_neg = tot_neg = 0
    per_class = {c: [0, 0] for c in MUST}
    missed = []
    ths = []
    for k in (0, 1):
        th = thresholds(folds[1 - k])
        ths.append(th)
        for i in folds[k]:
            g = gt[i]
            if g["must_keep"]:
                tot_pos += 1
                kept_pos += keep(i, th)
                if not keep(i, th):
                    missed.append(i)
                for c in MUST:
                    if c in g["classes"]:
                        per_class[c][1] += 1
                        per_class[c][0] += keep(i, th)
            else:
                tot_neg += 1
                kept_neg += keep(i, th)
    th_avg = {c: float(np.mean([t[c] for t in ths])) for c in MUST}
    walk_kept = sum(keep(i, th_avg) for i in walk)
    return {"recall": f"{kept_pos}/{tot_pos}", "recall_frac": kept_pos / max(1, tot_pos),
            "per_class": {c: f"{a}/{b}" for c, (a, b) in per_class.items()},
            "labelled_neg_filtered": f"{tot_neg - kept_neg}/{tot_neg}",
            "walk_filtered": f"{len(walk) - walk_kept}/{len(walk)}", "walk_filtered_frac": 1 - walk_kept / max(1, len(walk)),
            "thresholds": th_avg, "missed": missed}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--feat", default="/tmp/b3/feat")
    p.add_argument("--gates", default="clip,clipL,siglip,owlv2,dbnet_text")
    p.add_argument("--tag", default="")
    args = p.parse_args()
    want = set(args.gates.split(","))
    images = json.loads((HERE / "images.json").read_text())
    gt = json.loads((HERE / "results/gt.json").read_text())["frames"]
    ids = [r["id"] for r in images]
    gates = {}
    for kind in ("clip", "clipL", "siglip"):
        if kind in want and (Path(args.feat) / f"{kind}_emb.npz").exists():
            gates[kind] = embedding_scores(kind, args.feat, ids)
    if "owlv2" in want and (Path(args.feat) / "owlv2.jsonl").exists():
        gates["owlv2"] = owl_scores(args.feat)
    if "dbnet_text" in want and (Path(args.feat) / "dbnet.jsonl").exists():
        gates["dbnet_text"] = text_scores(args.feat)
    # Compare every gate on the same frames: the subset OWLv2 / DBNet ran on (subset.txt), when present.
    common = set.intersection(*(set(s) for s in gates.values()))
    gates = {k: {i: v[i] for i in common} for k, v in gates.items()}
    res = {}
    for name, s in gates.items():
        res[name] = {f"margin_{m}": evaluate(s, gt, images, m) for m in (1.0, 0.8, 0.5)}
    print(f"frames scored by every gate: {len(common)}")
    print("| gate | margin | must-keep recall | meter | panel | gas | AC | pool | labelled negatives filtered | walkaround frames filtered |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for name, r in res.items():
        for m, e in r.items():
            pc = e["per_class"]
            print(f"| {name} | {m} | {e['recall']} | {pc['meter']} | {pc['panel']} | {pc['gas_meter']} | {pc['ac_condenser']} | {pc['pool']} |"
                  f" {e['labelled_neg_filtered']} | {e['walk_filtered']} ({e['walk_filtered_frac']:.0%}) |")
    (HERE / f"results/gate_scores{args.tag}.json").write_text(json.dumps(gates))
    (HERE / f"results/gate_eval{args.tag}.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
