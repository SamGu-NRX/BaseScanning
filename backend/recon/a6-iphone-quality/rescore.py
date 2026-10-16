"""Rescore saved (P, Q, range) arrays by camera-range cohort; pool per method across segments/windows; write summary."""
import sys, os, glob, json, collections, numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from pairscore import score, stats, BINS, IN
HERE = os.path.dirname(__file__); ROOT = os.path.expanduser("~/lamar")
RANGES = {"<=6m": 6.0, "<=10m": 10.0, "<=20m": 20.0}
rows = collections.defaultdict(list)
per = {}
for f in sorted(glob.glob(f"{ROOT}/arrays/*/*.npz")):
    track = f.split("/")[-2]; seg, meth = os.path.basename(f)[:-4].split("__")
    meth = meth.split("_w")[0]; z = np.load(f)
    for rn, rmax in RANGES.items():
        m = z["rng"] <= rmax
        for variant, sc in (("arkit", 1.0), ("oracle", float(z["s_oracle"]))):
            r = score(sc * z["P"][m], z["Q"][m])
            per[f"{track}/{os.path.basename(f)[:-4]}/{rn}/{variant}"] = r
            rows[(track, meth, rn, variant)].append(r)
def pool(rs, key):
    # pool by median of per-run stats (each run = one segment window), plus worst run
    xs = [r[key] for r in rs if r.get(key)]
    if not xs: return None
    return dict(runs=len(xs), med_in=float(np.median([x["med_in"] for x in xs])), p90_in=float(np.median([x["p90_in"] for x in xs])),
                worst_p90_in=float(max(x["p90_in"] for x in xs)), le4=float(np.mean([x["le4"] for x in xs])), le6=float(np.mean([x["le6"] for x in xs])))
summ = {}
for k, rs in sorted(rows.items()):
    summ["|".join(k)] = {b: pool(rs, b) for b in BINS} | {"patch " + b: pool(rs, "patch " + b) for b in BINS} | {"outlier_frac_med": float(np.median([r.get("outlier_frac", np.nan) for r in rs])),
                                                         "scale_err_pct": [round(r.get("long_ratio_err_pct") or np.nan, 2) for r in rs]}
json.dump(dict(per_run=per, pooled=summ), open(f"{HERE}/results/rescored.json", "w"), indent=1)
L = "4.57-7.92m (15-26 ft)"; O = "0.5-2m (openings)"
print("| track | method | range | scale | 15-26 ft points median / p90 (worst run p90) | <=4 / <=6 in | 15-26 ft patches median / p90 (worst) | patch <=4 / <=6 in | openings points median / p90 | outliers | per-run long-pair scale err % |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
for k, v in summ.items():
    t, m, rn, var = k.split("|"); a = v[L]; b = v[O]; c = v["patch " + L] or dict(med_in=np.nan, p90_in=np.nan, worst_p90_in=np.nan, le4=np.nan, le6=np.nan)
    if not a: continue
    print(f"| {t} | {m} | {rn} | {var} | {a['med_in']:.1f} / {a['p90_in']:.1f} ({a['worst_p90_in']:.1f}) | {a['le4']*100:.0f} / {a['le6']*100:.0f}% | "
          f"{c['med_in']:.1f} / {c['p90_in']:.1f} ({c['worst_p90_in']:.1f}) | {c['le4']*100:.0f} / {c['le6']*100:.0f}% | "
          f"{b['med_in']:.1f} / {b['p90_in']:.1f} | {v['outlier_frac_med']*100:.0f}% | {v['scale_err_pct']} |")
