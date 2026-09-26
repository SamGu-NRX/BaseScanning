"""Alignment-free metric scoring: each reconstructed point P_k carries (GT view, pixel); its laser twin Q_k is that pixel
lifted with the GT pose and laser depth. Error of a distance = |P_i - P_j| - |Q_i - Q_j| (inches), binned by true length."""
import numpy as np
from lamar_io import umeyama
IN = 0.0254
BINS = {"0.5-2m (openings)": (0.5, 2.0), "2-4.57m": (2.0, 4.57), "4.57-7.92m (15-26 ft)": (4.57, 7.92)}

def stats(e):
    a = np.abs(e)
    if len(a) == 0: return None
    return dict(n=int(len(a)), med_in=float(np.median(a)), p90_in=float(np.percentile(a, 90)),
                le2=float((a <= 2).mean()), le4=float((a <= 4).mean()), le6=float((a <= 6).mean()), bias_in=float(np.median(e)))

def local_inliers(P, Q, inlier_m=0.30, cell_m=4.0, min_pts=30):
    """Flag gross local outliers (dynamic objects, sky/vegetation vs laser, wrong lookups) with a Sim3 fit per 4 m cell of
    laser space; large-scale bending of the reconstruction stays in the pair errors. Cells with too few points keep all."""
    inl = np.ones(len(P), bool)
    key = np.floor(Q / cell_m).astype(np.int64); _, cid = np.unique(key, axis=0, return_inverse=True); cid = cid.ravel()
    for c in np.unique(cid):
        m = np.where(cid == c)[0]
        if len(m) < min_pts: continue
        s, R, t = umeyama(P[m], Q[m]); r = np.linalg.norm(s * P[m] @ R.T + t - Q[m], axis=1)
        inl[m] = r < inlier_m
    return inl

def score(P, Q, inlier_m=0.30, max_pairs=300000, seed=0, max_range_Q=None):
    P = np.asarray(P, float); Q = np.asarray(Q, float)
    ok = np.isfinite(P).all(1) & np.isfinite(Q).all(1); P, Q = P[ok], Q[ok]
    out = dict(points=int(len(P)))
    if len(P) < 20: return out
    s, R, t = umeyama(P, Q); out["sim3_scale_err_pct"] = float((1 / s - 1) * 100)
    out["global_sim3_rms_m"] = float(np.sqrt(np.median(np.sum((s * P @ R.T + t - Q) ** 2, 1))))
    inl = local_inliers(P, Q, inlier_m); out["outlier_frac"] = float(1 - inl.mean())
    P, Q = P[inl], Q[inl]; n = len(P)
    rng = np.random.default_rng(seed)
    i = rng.integers(0, n, max_pairs); j = rng.integers(0, n, max_pairs); k = i != j; i, j = i[k], j[k]
    dq = np.linalg.norm(Q[i] - Q[j], axis=1); dp = np.linalg.norm(P[i] - P[j], axis=1)
    e = (dp - dq) / IN
    for name, (a, b) in BINS.items():
        m = (dq >= a) & (dq < b); out[name] = stats(e[m])
    # patch-averaged pairs: centroids of >=3 points per 25 cm laser voxel (KD-like: surfaces are fitted, not single points)
    key = np.floor(Q / 0.25).astype(np.int64); _, cid, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True); cid = cid.ravel()
    Pc = np.stack([np.bincount(cid, P[:, k]) for k in range(3)], 1) / cnt[:, None]
    Qc = np.stack([np.bincount(cid, Q[:, k]) for k in range(3)], 1) / cnt[:, None]
    keep = cnt >= 3; Pc, Qc = Pc[keep], Qc[keep]; out["patches"] = int(keep.sum())
    if len(Pc) > 10:
        ii, jj = np.triu_indices(len(Pc), 1)
        if len(ii) > max_pairs: sel = rng.choice(len(ii), max_pairs, replace=False); ii, jj = ii[sel], jj[sel]
        dqc = np.linalg.norm(Qc[ii] - Qc[jj], axis=1); ec = (np.linalg.norm(Pc[ii] - Pc[jj], axis=1) - dqc) / IN
        for name, (a, b) in BINS.items():
            m = (dqc >= a) & (dqc < b); out["patch " + name] = stats(ec[m])
    long = (dq >= 4.57) & (dq < 7.92)
    out["long_ratio_err_pct"] = float(np.median(dp[long] / dq[long] - 1) * 100) if long.any() else None
    return out

def fmt(r, key="4.57-7.92m (15-26 ft)"):
    x = r.get(key)
    return "n/a" if not x else f"{x['med_in']:.1f} / {x['p90_in']:.1f} in ({x['le4']*100:.0f}% <=4in)"
