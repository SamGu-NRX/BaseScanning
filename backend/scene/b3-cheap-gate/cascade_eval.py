"""Object tier: gate -> gpt-5.4-mini on kept frames -> GPT-5.5 on escalations, vs VLM on every frame.

    python cascade_eval.py --gate siglip --margin 0.5 [--esc unsure|unsure+gate]

Frame level (81 B1-labelled walkaround + Commons frames): per-class presence recall / precision of
- baseline: GPT-5.5 on every frame (same presence prompt, vlm_presence.py),
- mini on every frame,
- cascade: frames the gate drops count as "no"; kept frames get mini's answer; frames escalated
  (mini says "unsure" on an equipment class, or, with unsure+gate, the gate fires on an equipment
  class mini calls "no") get GPT-5.5's answer.
House level (11 houses, house.json `visible` yes/partial = present, unknown skipped): an object is
found if any frame's final answer says "yes". Needs GPT-5.5 presence on the escalated walkaround
frames (written by vlm_presence.py with --ids-file results/escalate_<gate>.txt).
Writes results/cascade_<gate>.json.
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = Path("/tmp/b3/raw/presence")
EQUIP = ["electric_meter", "main_panel", "gas_meter", "ac_condenser", "pool"]
TO_GT = {"electric_meter": "meter", "main_panel": "panel", "gas_meter": "gas_meter", "ac_condenser": "ac_condenser", "pool": "pool",
         "door": "door", "window": "window", "downspout": "downspout", "hose_bib": "hose_bib"}
GATE_CLS = {"meter": "electric_meter", "panel": "main_panel", "gas_meter": "gas_meter", "ac_condenser": "ac_condenser", "pool": "pool"}


def load(path):
    out = {}
    if Path(path).exists():
        for x in Path(path).read_text().splitlines():
            if x.strip():
                r = json.loads(x)
                if "error" not in r:
                    out[r["id"]] = r
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--gate", default="siglip")
    p.add_argument("--margin", default="margin_0.5")
    p.add_argument("--esc", default="unsure+gate")
    args = p.parse_args()
    images = json.loads((HERE / "images.json").read_text())
    gtall = json.loads((HERE / "results/gt.json").read_text())
    gt, houses = gtall["frames"], gtall["houses"]
    scores = json.loads((HERE / "results/gate_scores.json").read_text())[args.gate]
    th = json.loads((HERE / "results/gate_eval.json").read_text())[args.gate][args.margin]["thresholds"]
    mini, big = load(RAW / "gpt-5.4-mini.jsonl"), load(RAW / "gpt-5.5.jsonl")

    def fires(i):
        return {c for c in th if i in scores and scores[i][c] >= th[c]}

    def kept(i):
        return i not in scores or bool(fires(i))  # frames the gate did not score are kept (conservative)

    def escalate(i):
        m = mini.get(i)
        if not m:
            return True
        if any(m["present"][c] == "unsure" for c in EQUIP):
            return True
        if args.esc == "unsure+gate":
            return any(m["present"][GATE_CLS[c]] == "no" for c in fires(i))
        return False

    def final(i, system):
        if system == "baseline":
            return big.get(i), 1
        if system == "mini":
            return mini.get(i), 1
        if not kept(i):
            return None, 0
        if escalate(i):
            return (big.get(i) or mini.get(i)), 2
        return mini.get(i), 1

    lab = [r["id"] for r in images if r["set"] in ("walk", "commons") and gt[r["id"]]["labelled"] and r["id"] in big]
    frame = {}
    for system in ("baseline", "mini", "cascade"):
        res = {}
        for c, g in TO_GT.items():
            tp = fp = fn = 0
            for i in lab:
                a, _ = final(i, system)
                yes = bool(a) and a["present"].get(c) == "yes"
                truth = g in gt[i]["classes"]
                tp += yes and truth
                fp += yes and not truth
                fn += truth and not yes
            res[c] = {"tp": tp, "fp": fp, "fn": fn, "recall": f"{tp}/{tp + fn}", "precision": f"{tp}/{tp + fp}"}
        frame[system] = res

    walk = [r for r in images if r["set"] == "walk"]
    esc_ids = [r["id"] for r in walk if kept(r["id"]) and escalate(r["id"])]
    (HERE / "results" / f"escalate_{args.gate}.txt").write_text("\n".join(esc_ids))
    house_res = {}
    for system in ("mini", "cascade"):
        found = {}
        for h in houses:
            ids = [r["id"] for r in walk if r["house"] == h]
            for c in EQUIP:
                found[(h, c)] = any((final(i, system)[0] or {}).get("present", {}).get(c) == "yes" for i in ids)
        tp = fn = fp = tn = 0
        misses, falses = [], []
        for h, v in houses.items():
            for c in EQUIP:
                st = v[TO_GT[c]]["state"]
                if st == "unknown":
                    continue
                truth = st in ("yes", "partial")
                f = found[(h, c)]
                tp += truth and f
                fn += truth and not f
                fp += f and not truth
                tn += not truth and not f
                if truth and not f:
                    misses.append(f"{h}:{c}")
                if f and not truth:
                    falses.append(f"{h}:{c}")
        house_res[system] = {"recall": f"{tp}/{tp + fn}", "false_present": f"{fp}/{fp + tn}", "misses": misses, "false": falses}
    n_walk = len(walk)
    n_kept = sum(kept(r["id"]) for r in walk)
    have_big = sum(1 for i in esc_ids if i in big)
    cost = {
        "walk_frames": n_walk, "kept": n_kept, "escalated": len(esc_ids), "escalated_with_gpt55_answer": have_big,
        "mini_usd_per_frame": sum(r["cost_usd"] for r in mini.values()) / max(1, len(mini)),
        "gpt55_usd_per_frame": sum(r["cost_usd"] for r in big.values()) / max(1, len(big)),
        "mini_secs": sorted(r["secs"] for r in mini.values())[len(mini) // 2] if mini else None,
        "gpt55_secs": sorted(r["secs"] for r in big.values())[len(big) // 2] if big else None,
    }
    out = {"gate": args.gate, "margin": args.margin, "esc": args.esc, "frame": frame, "house": house_res, "cost": cost}
    (HERE / "results" / f"cascade_{args.gate}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({"house": house_res, "cost": cost}, indent=1))
    print("| class | " + " | ".join(f"{s} R / P" for s in frame) + " |")
    print("|---|" + "---|" * len(frame))
    for c in TO_GT:
        print(f"| {c} | " + " | ".join(f"{frame[s][c]['recall']} / {frame[s][c]['precision']}" for s in frame) + " |")


if __name__ == "__main__":
    main()
