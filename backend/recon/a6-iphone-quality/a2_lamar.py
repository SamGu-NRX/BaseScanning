"""A2 on real ARKit captures (LaMAR LIN): pi3 (images only; scale by Sim3 of its cameras to the real ARKit cameras)
and DA3-Large (fed real ARKit poses + intrinsics, and images only + the same Sim3). Reuses A2's adapters unchanged.
Windows: 16 consecutive GT-posed frames of a segment. Scored per window with the alignment-free laser pair scorer.
    python a2_lamar.py <segment id> [--models pi3 da3KT da3img] [--max-windows 3]"""
import sys, os, json, time, argparse, numpy as np
from PIL import Image
sys.path.insert(0, os.path.expanduser("~/a2/code")); sys.path.insert(0, os.path.dirname(__file__))
from a2ff.run import Pi3, DA3
from lamar_io import *
from laser import render_depth, lookup, backproject
from pairscore import score, fmt
HERE = os.path.dirname(__file__); NET = (378, 504); STEP = 6
gt = load_gt()

def window_inputs(seg, views):
    ak = load_arkit(seg["session"]); K = load_intrinsics(seg["session"]); rgbs, Kn, T = [], [], []
    for v in views:
        ts = v.split("/")[1][:-4]
        im = Image.open(f"{ROOT}/raw/{seg['session']}/raw_data/images/{ts}.jpg").convert("RGB")
        w, h = im.size; s = NET[0] / w
        rgbs.append(np.asarray(im.resize(NET, Image.BICUBIC)))
        _, _, fx, fy, cx, cy = K[ts]; Kn.append(np.array([fx * s, fy * s, cx * s, cy * s]))
        R_wc, c = ak[ts]; M = np.eye(4); M[:3, :3] = R_wc; M[:3, 3] = c; T.append(M)
    return rgbs, Kn, T, w, h

def world_points(p, kind):
    H, W = p["depth"].shape; v, u = np.mgrid[0:H:STEP, 0:W:STEP]; u = u.ravel(); v = v.ravel()
    z = p["depth"][v, u].astype(np.float64); m = np.isfinite(z) & (z > 0)
    if kind == "pi3" and p.get("mask") is not None: m &= p["mask"][v, u]
    fx, fy, cx, cy = p["K"]
    X = np.stack([(u - cx) / fx * z, (v - cy) / fy * z, z], 1)
    Xw = X @ p["c2w"][:3, :3].T + p["c2w"][:3, 3]
    return Xw[m], np.stack([u[m], v[m]], 1)

def laser_twins(view, uv_net, w, h, P_laser, cache):
    g = gt[view]; _, W, H, fx, fy, cx, cy = g["cam"]
    if view not in cache: cache[view] = render_depth(P_laser, g["R"], g["t"], [fx, fy, cx, cy], W, H)
    uv = (uv_net + 0.5) * (W / NET[0])
    z = lookup(cache[view], uv); ok = np.isfinite(z) & (z < 20)
    Q = np.full((len(uv), 3), np.nan); Q[ok] = backproject(uv[ok], z[ok], g["R"], g["t"], [fx, fy, cx, cy])
    return Q

def run(seg, adapter, kind, feed, views, P_laser, cache, ARR):
    rgbs, Kn, T, w, h = window_inputs(seg, views)
    t0 = time.perf_counter(); preds = adapter.infer(rgbs, Kn if feed else None, T if feed else None); dt = time.perf_counter() - t0
    Cm = np.array([p["c2w"][:3, 3] for p in preds]); Cak = np.array([t[:3, 3] for t in T])
    Cg = np.array([gt_center(gt[v]) for v in views])
    P, Q, RG = [], [], []
    for v, p in zip(views, preds):
        X, uvn = world_points(p, kind); q = laser_twins(v, uvn, w, h, P_laser, cache)
        P.append(X); Q.append(q); RG.append(np.linalg.norm(q - gt_center(gt[v]), axis=1))
    P = np.concatenate(P); Q = np.concatenate(Q); RG = np.concatenate(RG)
    s_gt, _, _ = umeyama(Cm, Cg)
    if feed: s_ak = 1.0  # already in the ARKit frame
    else: s_ak, _, _ = umeyama(Cm, Cak)
    np.savez_compressed(ARR, P=s_ak * P, Q=Q, rng=RG, s_oracle=s_gt / s_ak)
    r = dict(seconds=dt, arkit=score(s_ak * P, Q), oracle_scale=score(s_gt * P, Q),
             scale_vs_gt_pct=float((s_ak / s_gt - 1) * 100),
             arkit_window_scale_err_pct=float((1 / umeyama(Cak, Cg)[0] - 1) * 100))
    return r

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("seg"); ap.add_argument("--models", nargs="+", default=["pi3", "da3KT", "da3img"])
    ap.add_argument("--max-windows", type=int, default=3); a = ap.parse_args()
    seg = next(s for s in json.load(open(f"{HERE}/results/segments.json")) if s["id"] == a.seg)
    P_laser = np.load(f"{ROOT}/laser/{a.seg}.npz")["pts"]
    gv = seg["gt_views"]; nw = min(a.max_windows, len(gv) // 16)
    starts = np.linspace(0, len(gv) - 16, nw).astype(int) if nw > 1 else [max(0, (len(gv) - 16) // 2)]
    wins = [gv[s:s + 16] for s in starts]
    outp = f"{HERE}/results/a2/{a.seg}.json"; os.makedirs(os.path.dirname(outp), exist_ok=True)
    res = json.load(open(outp)) if os.path.exists(outp) else {}
    cache = {}
    for mk in a.models:
        if mk == "pi3": ad = Pi3(); kind, feed = "pi3", False
        else: ad = DA3("depth-anything/DA3-LARGE-1.1", "da3L", "Apache-2.0"); kind, feed = "da3", mk == "da3KT"
        ad.net = NET; ad.load()
        for wi, views in enumerate(wins):
            key = f"{mk}/w{wi}"
            if key in res: continue
            os.makedirs(f"{ROOT}/arrays/a2", exist_ok=True)
            r = run(seg, ad, kind, feed, views, P_laser, cache, f"{ROOT}/arrays/a2/{a.seg}__{mk}_w{wi}.npz"); r["views"] = [views[0], views[-1]]; res[key] = r
            print(key, f"{r['seconds']:.0f}s", "scale vs GT %.2f%%" % r["scale_vs_gt_pct"], "| ARKit long:", fmt(r["arkit"]),
                  "| openings:", fmt(r["arkit"], "0.5-2m (openings)"), "| oracle long:", fmt(r["oracle_scale"]), "| outl %.2f" % r["arkit"].get("outlier_frac", -1), flush=True)
            json.dump(res, open(outp, "w"), indent=1)
        del ad
