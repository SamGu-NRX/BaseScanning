"""Pick test segments (~35 m of walk) per session and their input frames (packet rule: every 0.5 m or 15 deg of ARKit motion)."""
import sys, os, json, numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from lamar_io import *
HERE = os.path.dirname(__file__)

def keyframes(session, t0, t1, step_m=0.5, step_deg=15.0):
    ak = load_arkit(session); ts = sorted(k for k in ak if t0 <= int(k) <= t1)
    imgs = set(os.listdir(f"{ROOT}/raw/{session}/raw_data/images"))
    ts = [t for t in ts if f"{t}.jpg" in imgs]
    keep = [ts[0]]
    for t in ts[1:]:
        R0, c0 = ak[keep[-1]]; R1, c1 = ak[t]
        ang = np.degrees(np.arccos(np.clip((np.trace(R0.T @ R1) - 1) / 2, -1, 1)))
        if np.linalg.norm(c1 - c0) >= step_m or ang >= step_deg: keep.append(t)
    return keep

def make(session, n_seg=2, length=35.0):
    rows = matched(session); G = np.array([r[2] for r in rows])
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(G, axis=0), axis=1))]
    segs = []; total = cum[-1]
    starts = np.linspace(0, max(total - length, 0), n_seg + 2)[1:-1] if n_seg > 1 else [max(total - length, 0) / 2]
    for k, s0 in enumerate(starts):
        a = int(np.searchsorted(cum, s0)); b = int(np.searchsorted(cum, s0 + length))
        b = min(b, len(rows) - 1)
        t0, t1 = rows[a][0], rows[b][0]
        kf = keyframes(session, t0, t1)
        gtv = [r[1] for r in rows[a:b + 1]]
        segs.append(dict(id=f"{session[4:]}_s{k}", session=session, t0=t0, t1=t1, path_m=float(cum[b] - cum[a]),
                         keyframes=kf, gt_views=gtv))
    return segs

if __name__ == "__main__":
    out = []
    for s, n in [("ios_2022-06-17_10.43.29", 2), ("ios_2022-02-10_14.47.24", 2), ("ios_2021-06-02_11.12.32", 1)]:
        for seg in make(s, n):
            gts = {v.split("/")[1][:-4] for v in seg["gt_views"]}
            print(seg["id"], round(seg["path_m"], 1), "keyframes", len(seg["keyframes"]), "gt views", len(seg["gt_views"]),
                  "gt in raw", len(gts & set(seg["keyframes"])), "/", len(gts)); out.append(seg)
    json.dump(out, open(f"{HERE}/results/segments.json", "w"), indent=1)
