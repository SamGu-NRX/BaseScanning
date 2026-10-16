"""Split of the LaMAR real-ARKit 15-26 ft baseline errors: depth recorded vs not, and time gap between the two frames."""
import sys, os, json, numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from lamar_io import *
gt = load_gt(); meta = json.load(open(f"{ROOT}/metadata_phone.json"))
acc = {}
for s in sorted({k.split("/")[0] for k in gt}):
    rows = matched(s, gt)
    if len(rows) < 10: continue
    T = np.array([r[0] for r in rows]) * 1e-6; G = np.array([r[2] for r in rows]); A = np.array([r[3] for r in rows])
    i, j = np.triu_indices(len(rows), 1); dt = T[j] - T[i]
    dg = np.linalg.norm(G[i] - G[j], axis=1); da = np.linalg.norm(A[i] - A[j], axis=1); e = np.abs(da - dg) / 0.0254
    band = (dg > 4.57) & (dg < 7.92)
    for tag, m in (("dt<15s", dt < 15), ("dt<30s", dt < 30), ("dt<60s", dt < 60), ("dt<120s", dt < 120)):
        for dk in ("all", "depth" if meta[s]["has_depth"] else "nodepth"):
            acc.setdefault((dk, tag), []).append(e[band & m])
out = {}
for (dk, tag), v in sorted(acc.items()):
    x = np.concatenate(v); out[f"{dk}|{tag}"] = dict(pairs=len(x), med_in=float(np.median(x)), p90_in=float(np.percentile(x, 90)), le4=float((x <= 4).mean()), le6=float((x <= 6).mean()))
    print(dk, tag, out[f"{dk}|{tag}"])
json.dump(out, open(f"{os.path.dirname(__file__)}/results/arkit_scale_lamar_split.json", "w"), indent=1)
