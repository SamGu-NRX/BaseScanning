"""Real non-LiDAR ARKit (iPhone 6s, ARKit 1, 2018) vs ADVIO's inertial+fix-point GT: 15-26 ft baseline errors."""
import os, sys, json, numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from lamar_io import umeyama
D = os.path.expanduser("~/advio"); res = []; allp = {"indoor": [], "outdoor": []}
for i in range(1, 24):
    a = np.loadtxt(f"{D}/advio-{i:02d}/iphone/arkit.csv", delimiter=","); g = np.loadtxt(f"{D}/advio-{i:02d}/ground-truth/pose.csv", delimiter=",")
    t = np.arange(max(a[0, 0], g[0, 0]), min(a[-1, 0], g[-1, 0]), 0.2)
    A = np.stack([np.interp(t, a[:, 0], a[:, k]) for k in (1, 2, 3)], 1); G = np.stack([np.interp(t, g[:, 0], g[:, k]) for k in (1, 2, 3)], 1)
    s, R, tt = umeyama(A, G)
    ii, jj = np.triu_indices(len(t), 1); dt = t[jj] - t[ii]
    dg = np.linalg.norm(G[ii] - G[jj], axis=1); da = np.linalg.norm(A[ii] - A[jj], axis=1)
    m = (dg > 4.57) & (dg < 7.92) & (dt < 60); e = (da[m] - dg[m]) / 0.0254
    io = "outdoor" if i >= 20 else "indoor"; allp[io].append(e)
    r = dict(seq=i, env=io, dur_s=float(t[-1] - t[0]), path_m=float(np.linalg.norm(np.diff(G, axis=0), axis=1).sum()),
             session_scale_err_pct=float((1 / s - 1) * 100), pairs=int(m.sum()),
             med_in=float(np.median(np.abs(e))), p90_in=float(np.percentile(np.abs(e), 90)), le4=float((np.abs(e) <= 4).mean()))
    res.append(r); print(json.dumps(r))
summ = {}
for k, v in allp.items():
    x = np.abs(np.concatenate(v)); summ[k] = dict(pairs=len(x), med_in=float(np.median(x)), p90_in=float(np.percentile(x, 90)),
                                                  le2=float((x <= 2).mean()), le4=float((x <= 4).mean()), le6=float((x <= 6).mean()))
print("SUMMARY", json.dumps(summ))
json.dump(dict(per_seq=res, summary=summ), open(f"{os.path.dirname(__file__)}/results/arkit_scale_advio.json", "w"), indent=1)
