"""A10: choose N photos from an ETH3D scene the way a homeowner would, guided or careless.

    python select.py <gt scene.json> <site> <N> [<N> ...]   -> selections/<site>.json

Geometry comes from the GT scene (meter-anchored frame: meter at the origin, meter wall w1 along x,
+z out of the wall, +y up; keyframe poses are ARKit camera-to-world). Visibility here is frustum +
incidence only (no occlusion); the shared scorer's visibility oracle judges the truth afterwards.

Criteria regions sampled (docs/criteria-spec.md 4.4, harness completeness bands):
  meter_wall  w1 within 8 m of the meter, 0-2.13 m high
  adjacent    every other chain wall within 8 m (along the chain) of the meter's wall ends, 0-2.13 m
  ground      0-4.27 m out from every chain wall, within 8 m of the meter along the chain
  overhead    0.9 m out, 2.1-3 m high above the meter wall strip
  meter       the meter point itself (close-up = nearest frontal view that sees it)

guided   = the simplest shot list: one meter close-up, then greedy picks that most increase the number
           of region samples seen by >= 2 views at <= 75 deg (walls) / <= 80 deg (ground), with a
           baseline tie-break; this is "walk the wall slowly, face each side, step back for the ground".
careless = a homeowner who stays near the meter: every photo taken from the N camera positions nearest
           one random standing spot among the views that see the meter (two seeds).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MAX_DIST = 30.0


def mat(v):
    return np.asarray(v, float).reshape(4, 4, order="F")


def wall_samples(scene, max_s=8.0):
    walls = {w["id"]: w for w in scene["walls"]}
    chain = scene["chain"]["wall_ids"]
    out = {"meter_wall": [], "adjacent": [], "ground": [], "overhead": []}
    s0 = 0.0
    for wid in chain:
        w = walls[wid]
        a, b = np.array(w["baseline_m"][0]), np.array(w["baseline_m"][1])
        L = float(np.linalg.norm(b - a))
        d = (b - a) / L
        n = np.array([-d[1], d[0]])  # left normal in plan (x, z)
        n = n if wid != "w1" else np.array([0.0, 1.0])
        s_start = w.get("s_range_m", [s0, s0 + L])[0]
        for t in np.arange(0.1, L, 0.25):
            s = s_start + t
            if abs(s) > max_s + 3.0:
                continue
            p = a + d * t
            if wid == "w1" and abs(s) > max_s:
                continue
            for h in (0.3, 1.0, 1.7):
                out["meter_wall" if wid == "w1" else "adjacent"].append(((p[0], h, p[1]), (n[0], 0, n[1])))
            if abs(s) <= max_s:
                for o in (0.5, 1.5, 2.5, 3.5, 4.2):
                    q = p + n * o
                    out["ground"].append(((q[0], 0.0, q[1]), (0, 1, 0)))
            if wid == "w1" and abs(s) <= 3.0:
                q = p + n * 0.9
                out["overhead"].append(((q[0], 2.5, q[1]), (0, -1, 0)))
        s0 = s_start + L
    return {k: (np.array([x[0] for x in v]), np.array([x[1] for x in v])) for k, v in out.items() if v}


def seen(kf, P, N, max_inc):
    T = mat(kf["pose"])
    C, R = T[:3, 3], T[:3, :3]
    fx, fy, cx, cy = kf["intrinsics"]
    v = P - C
    dist = np.linalg.norm(v, axis=1)
    pc = v @ R  # camera coords, ARKit: looks along -z
    z = -pc[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = fx * pc[:, 0] / z + cx
        w = -fy * pc[:, 1] / z + cy
    inc = np.degrees(np.arccos(np.clip(np.sum(-v / dist[:, None] * N, 1), -1, 1)))
    return (z > 0.3) & (u >= 0) & (u < kf["w"]) & (w >= 0) & (w < kf["h"]) & (dist < MAX_DIST) & (inc <= max_inc)


def build(scene):
    S = wall_samples(scene)
    kfs = scene["keyframes"]
    vis = {}
    for reg, (P, N) in S.items():
        lim = 80.0 if reg == "ground" else 75.0
        vis[reg] = np.array([seen(k, P, N, lim) for k in kfs])  # cams x samples
    meter = np.array([[0.0, 1.5, 0.0]])
    mv = np.array([seen(k, meter, np.array([[0, 0, 1.0]]), 60.0)[0] for k in kfs])
    C = np.array([mat(k["pose"])[:3, 3] for k in kfs])
    return S, vis, mv, C


def coverage(vis, sel):
    return {reg: float(np.mean(v[sel].sum(0) >= 2)) for reg, v in vis.items()}


def guided(scene, n):
    S, vis, mv, C = build(scene)
    ids = [k["id"] for k in scene["keyframes"]]
    n = min(n, len(ids))
    weights = {"meter_wall": 3.0, "adjacent": 1.5, "ground": 2.0, "overhead": 1.0}
    cand = np.flatnonzero(mv)
    sel = []
    if len(cand):
        sel.append(int(cand[np.argmin(np.linalg.norm(C[cand], axis=1))]))  # meter close-up
    while len(sel) < n:
        best, bi = -1.0, None
        cnt = {r: v[sel].sum(0) if sel else np.zeros(v.shape[1]) for r, v in vis.items()}
        for i in range(len(ids)):
            if i in sel:
                continue
            g = 0.0
            for r, v in vis.items():
                c = cnt[r]
                g += weights[r] * (np.sum(v[i] & (c == 1)) * 1.0 + np.sum(v[i] & (c == 0)) * 0.5 + np.sum(v[i] & (c >= 2)) * 0.02) / v.shape[1]
            base = min(np.linalg.norm(C[sel] - C[i], axis=1)) if sel else 0.0
            g += 1e-3 * min(base, 5.0)
            if g > best:
                best, bi = g, i
        sel.append(bi)
    return [ids[i] for i in sel], coverage(vis, sel)


def careless(scene, n, seed):
    S, vis, mv, C = build(scene)
    ids = [k["id"] for k in scene["keyframes"]]
    n = min(n, len(ids))
    rng = np.random.default_rng(seed)
    pool = np.flatnonzero(mv) if mv.any() else np.arange(len(ids))
    anchor = C[rng.choice(pool)]
    order = np.argsort(np.linalg.norm(C - anchor, axis=1))
    sel = [int(i) for i in order[:n]]
    return [ids[i] for i in sel], coverage(vis, sel)


def main():
    gt, site, Ns = Path(sys.argv[1]), sys.argv[2], [int(x) for x in sys.argv[3:]]
    scene = json.loads(gt.read_text())
    out = {"site": site, "n_available": len(scene["keyframes"]), "sets": {}}
    _, vis, mv, _ = build(scene)
    allsel = list(range(len(scene["keyframes"])))
    out["all_coverage"] = coverage(vis, allsel)
    out["meter_views_available"] = int(mv.sum())
    for n in Ns:
        tag = f"N{n}" if n < len(scene["keyframes"]) else f"all{len(scene['keyframes'])}"
        g, cg = guided(scene, n)
        out["sets"][f"{tag}_guided"] = {"n": len(g), "images": g, "coverage_2views": cg}
        for s in (0, 1):
            c, cc = careless(scene, n, s)
            out["sets"][f"{tag}_careless{s}"] = {"n": len(c), "images": c, "coverage_2views": cc}
    dest = HERE / "selections" / f"{site}.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(out, indent=1))
    for k, v in out["sets"].items():
        print(site, k, v["n"], {r: round(x, 2) for r, x in v["coverage_2views"].items()})
    print(site, "all", {r: round(x, 2) for r, x in out["all_coverage"].items()}, "meter views", out["meter_views_available"])


if __name__ == "__main__":
    main()
