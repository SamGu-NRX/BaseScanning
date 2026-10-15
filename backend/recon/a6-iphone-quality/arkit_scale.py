"""Real ARKit scale/drift vs laser-registered GT on every LaMAR LIN iOS session."""
import sys, json, os, numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from lamar_io import *
gt = load_gt(); sessions = sorted({k.split("/")[0] for k in gt})
meta = json.load(open(f"{ROOT}/metadata_phone.json")) if os.path.exists(f"{ROOT}/metadata_phone.json") else {}
res = []; pairs_all = []
for s in sessions:
    rows = matched(s, gt)
    if len(rows) < 10: continue
    T = np.array([r[0] for r in rows]) * 1e-6; G = np.array([r[2] for r in rows]); A = np.array([r[3] for r in rows])
    path = np.linalg.norm(np.diff(G, axis=0), axis=1).sum()
    sc, R, t = umeyama(A, G); rms = np.sqrt(((sc * A @ R.T + t - G) ** 2).sum(1).mean())
    # ARKit length / true length on 15-26 ft (4.57-7.92 m) baselines between frames <= 60 s apart
    i, j = np.triu_indices(len(rows), 1); dt = T[j] - T[i]
    dg = np.linalg.norm(G[i] - G[j], axis=1); da = np.linalg.norm(A[i] - A[j], axis=1)
    m = (dg > 4.57) & (dg < 7.92) & (dt < 60)
    ratio = da[m] / dg[m] - 1
    errin = (da[m] - dg[m]) / 0.0254
    # 30 m path windows: sim3 per window
    wins = []; cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(G, axis=0), axis=1))]
    for a in range(0, len(rows)):
        b = np.searchsorted(cum, cum[a] + 30)
        if b >= len(rows): break
        if b - a < 8: continue
        s2, _, _ = umeyama(A[a:b], G[a:b]); wins.append(1 / s2 - 1)
    wins = np.array(wins)
    r = dict(session=s, n=len(rows), dur_s=float(T[-1] - T[0]), path_m=float(path), depth=meta.get(s, {}).get("has_depth"),
             session_scale_err_pct=float((1 / sc - 1) * 100), sim3_rms_m=float(rms),
             win30_scale_err_pct_med=float(np.median(np.abs(wins)) * 100) if len(wins) else None,
             win30_scale_err_pct_p90=float(np.percentile(np.abs(wins), 90) * 100) if len(wins) else None,
             pairs_15_26ft=int(m.sum()),
             pair_abs_err_in_med=float(np.median(np.abs(errin))) if m.any() else None,
             pair_abs_err_in_p90=float(np.percentile(np.abs(errin), 90)) if m.any() else None)
    res.append(r); pairs_all.append(errin); print(json.dumps(r), flush=True)
allp = np.abs(np.concatenate(pairs_all))
summ = dict(sessions=len(res), pairs=len(allp), pair_abs_err_in_med=float(np.median(allp)), p90=float(np.percentile(allp, 90)),
            within_2=float((allp <= 2).mean()), within_4=float((allp <= 4).mean()), within_6=float((allp <= 6).mean()),
            session_scale_abs_med=float(np.median([abs(r["session_scale_err_pct"]) for r in res])),
            session_scale_abs_p90=float(np.percentile([abs(r["session_scale_err_pct"]) for r in res], 90)))
print("SUMMARY", json.dumps(summ))
os.makedirs(os.path.dirname(__file__) + "/results", exist_ok=True)
json.dump(dict(per_session=res, summary=summ), open(os.path.dirname(__file__) + "/results/arkit_scale_lamar_lin.json", "w"), indent=1)
