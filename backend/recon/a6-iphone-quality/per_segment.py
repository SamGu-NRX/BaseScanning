"""Per-segment table (<=10 m from camera, patch-averaged and point pairs) for the report."""
import json, os, collections, numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(f"{HERE}/results/rescored.json"))["per_run"]
L = "4.57-7.92m (15-26 ft)"; O = "0.5-2m (openings)"
agg = collections.defaultdict(list)
for k, r in d.items():
    track, run, rng, var = k.split("/")
    if rng != "<=10m": continue
    seg, meth = run.split("__"); meth = meth.split("_w")[0]
    agg[(seg, f"{track}:{meth}", var)].append(r)
segs = sorted({k[0] for k in agg}); meths = ["a1:gt", "a1:prior_s03", "a1:fixed", "a1:prior", "a1:noprior", "a2:pi3", "a2:da3KT", "a2:da3img"]
print("| segment | method | 15-26 ft patches, ARKit scale: median / p90 in | same, oracle scale | openings 0.5-2 m patches median / p90 | long-pair scale err % |")
print("|---|---|---|---|---|---|")
for s in segs:
    for m in meths:
        a = agg.get((s, m, "arkit")); o = agg.get((s, m, "oracle"))
        if not a: continue
        f = lambda rs, key: "n/a" if not all(r.get(key) for r in rs) else f"{np.median([r[key]['med_in'] for r in rs]):.1f} / {np.median([r[key]['p90_in'] for r in rs]):.1f}"
        sc = ", ".join(f"{r['long_ratio_err_pct']:+.1f}" for r in a if r.get("long_ratio_err_pct") is not None)
        print(f"| {s} | {m} | {f(a, 'patch ' + L)} | {f(o, 'patch ' + L)} | {f(a, 'patch ' + O)} | {sc} |")
