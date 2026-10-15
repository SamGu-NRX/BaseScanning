"""House-level object presence with a per-house frame budget chosen by a CPU gate.

    python select_eval.py --scores results/gate_scores_all.json [--pool all|subset]

For each house and equipment class (meter, panel, gas meter, AC, pool) the gate ranks the walkaround
frames by its class score and picks the top K, skipping frames within 2 positions of one already
picked for that class (temporal diversity). The union S goes to the VLM tier:
- "S -> GPT-5.5": GPT-5.5 presence on S.
- "S -> mini, GPT-5.5 confirms": mini on S; each equipment "yes" or "unsure" from mini is re-asked to
  GPT-5.5, whose answer is final; mini "no" is final.
- "S -> mini": mini alone.
Compared with GPT-5.5 on every frame (all-VLM baseline), mini on every frame, and a random selection
of the same size (seeded, 20 draws, mean). Truth: house.json `visible` (yes/partial present, no
absent, unknown skipped). Pool "subset" restricts every gate to the frames OWLv2/DBNet ran on.
Writes results/select_eval.json.
"""

import argparse
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = Path("/tmp/b3/raw/presence")
EQUIP = {"meter": "electric_meter", "panel": "main_panel", "gas_meter": "gas_meter", "ac_condenser": "ac_condenser", "pool": "pool"}


def load(path):
    out = {}
    for x in Path(path).read_text().splitlines():
        if x.strip():
            r = json.loads(x)
            if "error" not in r:
                out[r["id"]] = r
    return out


def yes(r, c):
    return bool(r) and r["present"].get(c) == "yes"


HAZARD_PICKS = {}  # house -> frames picked for gas meter / pool (always sent to GPT-5.5)


def select(frames, score, k):
    chosen = set()
    for g in EQUIP:
        order = sorted(range(len(frames)), key=lambda j: -score[frames[j]][g])
        picked = []
        for j in order:
            if len(picked) >= k:
                break
            if all(abs(j - q) > 2 for q in picked):
                picked.append(j)
        chosen |= {frames[j] for j in picked}
        if g in ("gas_meter", "pool"):
            HAZARD_PICKS.setdefault(frames[0].split("/")[0], set()).update(frames[j] for j in picked)
    return chosen


STRICT = False  # --strict: only "yes" counts as present; "partial" is skipped (neither present nor absent)


def house_eval(found, houses):
    tp = fn = fp = tn = 0
    misses, falses = [], []
    for h, v in houses.items():
        for g, c in EQUIP.items():
            st = v[g]["state"]
            if st == "unknown" or (STRICT and st == "partial"):
                continue
            truth = st in ("yes", "partial")
            f = found.get((h, c), False)
            tp += truth and f
            fn += truth and not f
            fp += f and not truth
            tn += (not truth) and not f
            if truth and not f:
                misses.append(f"{h}:{g}")
            if f and not truth:
                falses.append(f"{h}:{g}")
    return {"recall": f"{tp}/{tp + fn}", "false_present": f"{fp}/{fp + tn}", "misses": misses, "false": falses, "tp": tp, "fp": fp}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scores", nargs="+", default=[str(HERE / "results/gate_scores_all.json")])
    p.add_argument("--pool", default="all")
    p.add_argument("--ks", default="1,2,3,5")
    p.add_argument("--strict", action="store_true")
    args = p.parse_args()
    global STRICT
    STRICT = args.strict
    images = json.loads((HERE / "images.json").read_text())
    houses = json.loads((HERE / "results/gt.json").read_text())["houses"]
    mini, big = load(RAW / "gpt-5.4-mini.jsonl"), load(RAW / "gpt-5.5.jsonl")
    subset = set((HERE / "subset.txt").read_text().split())
    gates = {}
    for s in args.scores:
        gates.update(json.loads(Path(s).read_text()))
    walk = {}
    for r in images:
        if r["set"] == "walk" and r["id"] in mini and r["id"] in big and (args.pool == "all" or r["id"] in subset):
            walk.setdefault(r["house"], []).append(r["id"])
    n_frames = sum(len(v) for v in walk.values())
    c_mini = sum(mini[i]["cost_usd"] for v in walk.values() for i in v) / n_frames
    c_big = sum(big[i]["cost_usd"] for v in walk.values() for i in v) / n_frames
    rows = []

    def run(name, sel):
        """sel: house -> set of frame ids."""
        n_sel = sum(len(s) for s in sel.values())
        f_big, f_mini, f_casc, f_safe = {}, {}, {}, {}
        n_conf = n_safe = 0
        for h, s in sel.items():
            hz = HAZARD_PICKS.get(h, set()) & s if name != "every frame (all-VLM)" else set()
            for c in EQUIP.values():
                f_big[(h, c)] = any(yes(big[i], c) for i in s)
                f_mini[(h, c)] = any(yes(mini[i], c) for i in s)
                asked = {i for i in s if mini[i]["present"].get(c) in ("yes", "unsure")}
                f_casc[(h, c)] = any(yes(big[i], c) for i in asked)
                f_safe[(h, c)] = any(yes(big[i], c) for i in asked | hz)
            conf = {i for i in s if any(mini[i]["present"].get(c) in ("yes", "unsure") for c in EQUIP.values())}
            n_conf += len(conf)
            n_safe += len(conf | hz)
        per_house = n_frames / len(walk)
        for tier, found, usd in (("GPT-5.5", f_big, n_sel * c_big),
                                 ("mini, GPT-5.5 confirms", f_casc, n_sel * c_mini + n_conf * c_big),
                                 ("mini, GPT-5.5 confirms + gas/pool picks", f_safe, n_sel * c_mini + n_safe * c_big),
                                 ("mini", f_mini, n_sel * c_mini)):
            e = house_eval(found, houses)
            rows.append({"selection": name, "tier": tier, "frames_sent": f"{n_sel}/{n_frames}", "filtered": f"{1 - n_sel / n_frames:.0%}",
                         "house_recall": e["recall"], "false_present": e["false_present"],
                         "usd_per_house": usd / len(walk), "misses": " ".join(e["misses"]), "false": " ".join(e["false"])})
        return per_house

    run("every frame (all-VLM)", {h: set(v) for h, v in walk.items()})
    for k in [int(x) for x in args.ks.split(",")]:
        for g, score in gates.items():
            if not all(i in score for v in walk.values() for i in v):
                continue
            HAZARD_PICKS.clear()
            run(f"{g} top-{k}/class", {h: select(v, score, k) for h, v in walk.items()})
        if "clip" in gates and "siglip" in gates and all(i in gates["clip"] and i in gates["siglip"] for v in walk.values() for i in v):
            HAZARD_PICKS.clear()
            run(f"clip+siglip union top-{k}/class",
                {h: select(v, gates["clip"], k) | select(v, gates["siglip"], k) for h, v in walk.items()})
        # Random selection with the same per-house budget as the gate would take (5 classes x k, minus overlaps ~ 5k).
        rnd = random.Random(0)
        tps = []
        for _ in range(20):
            sel = {h: set(rnd.sample(v, min(len(v), 5 * k))) for h, v in walk.items()}
            found = {(h, c): any(yes(big[i], c) for i in s) for h, s in sel.items() for c in EQUIP.values()}
            tps.append(house_eval(found, houses)["tp"])
        truth_n = house_eval({}, houses)
        rows.append({"selection": f"random {5 * k}/house (20 draws)", "tier": "GPT-5.5", "frames_sent": f"{5 * k * len(walk)}/{n_frames}",
                     "filtered": f"{1 - 5 * k * len(walk) / n_frames:.0%}", "house_recall": f"{sum(tps) / 20:.1f}/{truth_n['recall'].split('/')[1]}",
                     "false_present": "-", "usd_per_house": 5 * k * c_big, "misses": "", "false": ""})
    keys = ["selection", "tier", "frames_sent", "filtered", "house_recall", "false_present", "usd_per_house", "misses", "false"]
    print(f"pool={args.pool}: {n_frames} walkaround frames in {len(walk)} houses; mini ${c_mini:.5f}/frame, GPT-5.5 ${c_big:.5f}/frame")
    print("| " + " | ".join(keys) + " |\n|" + "---|" * len(keys))
    for r in rows:
        print("| " + " | ".join(f"{r[k]:.3f}" if isinstance(r[k], float) else str(r[k]) for k in keys) + " |")
    (HERE / "results" / f"select_eval_{args.pool}{'_strict' if args.strict else ''}.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
