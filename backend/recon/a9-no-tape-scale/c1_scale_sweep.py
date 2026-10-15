"""Per-criterion decidability under scale uncertainty, on C1's real-geometry scenes.

For every scene x profile x scale level b (the 95 % relative scale bound the verdict carries), the
scene is rescaled about the meter by a true error e and perturbed with 2 in (p90) shape noise, and
the engine is told plus_minus = max(gt_pm, shape_p90 + b * distance_from_meter). Every candidate is
matched to the ground-truth candidate at the same wall fraction, and every check is compared:
kept (same decided outcome), lost (decided in truth -> unsure), wrong (pass <-> fail).

Trials per level: e = -b, e = +b (tail cases), and two draws e ~ N(0, b / 2.5).

    python c1_scale_sweep.py --workers 4   ->  results/c1_scale_sweep.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
C1 = Path(os.environ.get("C1_DIR", HERE.parent / "c1-rules-3d"))
sys.path.insert(0, str(C1))
os.chdir(C1)

import engine3d as E  # noqa: E402
import sweep as S  # noqa: E402
from run import prof  # noqa: E402

SHAPE_IN = 2.0
LEVELS = [0.0, 0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.06, 0.08, 0.12, 0.18]
SCENES = ["facade-M1", "facade-M2", "facade-M3", "electro-E1", "delivery_area-D1", "delivery_area-D3"]
PROFILES = ["public", "A", "B", "C", "strictest"]


class BoundPerturber(S.Perturber):
    """C1's perturber with the scale bound decoupled from the drawn error."""

    def __init__(self, d, level, rng, mode):
        super().__init__(d, level, rng, mode)
        self.b = level["bound"]

    def pm_told(self, p, kind, gt_pm):
        return max(gt_pm, self.p90(p, kind) + self.b * float(np.linalg.norm(np.asarray(p) - self.m)))


def perturb(d0, level, seed):
    orig = S.Perturber
    S.Perturber = BoundPerturber
    try:
        return S.perturb(d0, level, seed, "informed")
    finally:
        S.Perturber = orig


def cand_map(r):
    out = {}
    for c in r["_cands"]:
        out.setdefault(c.wall, []).append(c)
    return out


def job(args):
    scene, pname, b, trial = args
    d0 = json.loads((C1 / "outputs/scenes" / f"{scene}.json").read_text())
    p = prof(pname)
    rng = np.random.default_rng(1000 * trial + int(b * 1e4) + 7)
    e = {0: -b, 1: b}.get(trial, float(rng.normal(0, b / 2.5)))
    level = {"name": f"b{b}", "scale": e, "bound": b, "wall_in": SHAPE_IN, "edge_in": SHAPE_IN}
    gt = E.decide(E.load_scene(d0), p, step=1 / 3)
    dp = perturb(d0, level, 17 + trial)
    sp = E.load_scene(dp)
    pr = E.decide(sp, p, step=1 / 3)
    gsc = E.load_scene(d0)
    gm, pm = cand_map(gt), cand_map(pr)
    kept, lost, wrong, n = Counter(), Counter(), Counter(), Counter()
    cand = Counter()
    for wid, gcs in gm.items():
        pcs = pm.get(wid, [])
        if not pcs:
            continue
        k = gsc.walls[wid].L / sp.walls[wid].L
        pu = np.array([c.u0 * k for c in pcs])
        for gc in gcs:
            j = int(np.argmin(np.abs(pu - gc.u0)))
            if abs(pu[j] - gc.u0) > 0.34:
                continue
            pc = pcs[j]
            po = {c.id: c.outcome for c in pc.checks}
            cand[f"{gc.outcome}->{pc.outcome}"] += 1
            for c in gc.checks:
                if c.outcome == "unsure" or c.id not in po:
                    continue
                n[c.id] += 1
                q = po[c.id]
                if q == c.outcome:
                    kept[c.id] += 1
                elif q == "unsure":
                    lost[c.id] += 1
                else:
                    wrong[c.id] += 1
    gt_spot = None
    if pr["decision"] == "pass" and pr["best"] is not None:
        bb = pr["best"]
        k = gsc.walls[bb["wall"]].L / sp.walls[bb["wall"]].L
        u0 = min(max(0.0, bb["u"][0] * k), gsc.walls[bb["wall"]].L - p["battery"]["width_ft"])
        gt_spot = E.Solver3D(gsc, p).evaluate(gsc.walls[bb["wall"]], u0).outcome
    return {"scene": scene, "profile": pname, "bound": b, "trial": trial, "scale_err": e,
            "gt_decision": gt["decision"], "decision": pr["decision"], "gt_spot": gt_spot,
            "n": dict(n), "kept": dict(kept), "lost": dict(lost), "wrong": dict(wrong), "cand": dict(cand)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--trials", type=int, default=4)
    ap.add_argument("--out", default=str(HERE / "results/c1_scale_sweep.jsonl"))
    a = ap.parse_args()
    jobs = [(s, p, b, t) for s in SCENES for p in PROFILES for b in LEVELS for t in range(a.trials)
            if not (b == 0 and t > 0)]
    with open(a.out, "w") as f, ProcessPoolExecutor(a.workers) as ex:
        for i, r in enumerate(ex.map(job, jobs, chunksize=2)):
            f.write(json.dumps(r) + "\n")
            if i % 50 == 0:
                print(i, len(jobs), flush=True)
    print("done")


if __name__ == "__main__":
    main()
