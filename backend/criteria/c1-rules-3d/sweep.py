"""Error sweep: perturb ground-truth scene3d files, re-run the engine, count verdict flips and unsafe
passes against the ground-truth verdict.

Perturbation model (all lengths ft; p90 = 90th percentile of the displacement magnitude):
- wall vertices (joints and free ends) move by a 2D Gaussian with the level's wall p90; walls are
  re-derived from the moved vertices;
- everything else is carried in the wall-local frame of its nearest wall (u scaled with the wall's
  new length, offset kept): wall-mounted openings move per edge (1D Gaussian, edge p90) and in
  height; standing objects, obstacles, overheads, coverage and interior polygons ride with their
  wall, standing objects and obstacles add their own 2D noise at the edge p90;
- global scale about the meter multiplies every plan offset and height;
- ARKit-style drift: p90 grows with distance from the meter (PR #12 / packet-generator table),
  half shared direction, half independent per point.
Modes: `informed` tells the engine the p90 as plus_minus_ft (per element, distance-aware);
`naive` keeps the ground-truth plus_minus_ft (the capture does not know its own error).

    python sweep.py --trials 4 --workers 4
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import resource
import signal
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

import engine3d as E
from run import prof

HERE = Path(__file__).resolve().parent
P90_2D = math.sqrt(2 * math.log(10))  # radius p90 of a unit isotropic 2D Gaussian
P90_1D = 1.6449  # |x| p90 of a unit 1D Gaussian
IN = 1 / 12

DRIFT_AT = [3.0, 10.0, 20.0, 30.0]
DRIFT = {  # p90 inches at 3 / 10 / 20 / 30 ft from the start (internal/packet-generator/calibration.md)
    "lidar_modern": [4.4, 8.7, 13.5, 17.3],
    "nonlidar_modern": [5.4, 11.5, 19.2, 26.7],
    "nonlidar_2018": [8.7, 22.8, 44.1, 66.5],
}

LEVELS = (
    [{"name": f"uniform_{x}in", "wall_in": x, "edge_in": x} for x in (0, 2, 4, 6, 8, 12, 18, 27, 36)]
    + [{"name": f"scale_{s:+d}pct", "scale": s / 100} for s in (-10, -5, -2, -1, 1, 2, 5, 10)]
    + [
        {"name": "mvd_good", "wall_in": 2.8, "edge_in": 8.0},
        {"name": "mvd_mid", "wall_in": 5.0, "edge_in": 8.0},
        {"name": "mvd_poor", "wall_in": 10.8, "edge_in": 10.8},
        {"name": "photo_tape", "wall_in": 15.1, "edge_in": 26.9},
        {"name": "arkit_lidar_modern", "drift": "lidar_modern", "scale_range": [-0.02, 0.02]},
        {"name": "arkit_nonlidar_modern", "drift": "nonlidar_modern", "scale_range": [-0.02, 0.02]},
        {"name": "arkit_nonlidar_2018", "drift": "nonlidar_2018", "scale_range": [-0.16, -0.05]},
    ]
)


def drift_p90_ft(kind: str, d: float) -> float:
    xs, ys = DRIFT_AT, DRIFT[kind]
    if d <= xs[-1]:
        return float(np.interp(d, xs, ys)) * IN
    slope = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
    return (ys[-1] + slope * (d - xs[-1])) * IN


class Perturber:
    def __init__(self, d: dict, level: dict, rng: np.random.Generator, mode: str):
        self.d, self.lv, self.rng, self.mode = d, level, rng, mode
        self.m = np.array(d["meter"]["pos"], float)[[0, 2]]
        s = level.get("scale", 0.0)
        if "scale_range" in level:
            s = float(rng.uniform(*level["scale_range"]))
        self.s = s
        self.common = rng.normal(size=2)

    def p90(self, p, kind: str) -> float:
        lv = self.lv
        if "drift" in lv:
            return drift_p90_ft(lv["drift"], float(np.linalg.norm(np.asarray(p) - self.m)))
        return lv.get("wall_in" if kind == "wall" else "edge_in", 0.0) * IN

    def pm_told(self, p, kind: str, gt_pm: float) -> float:
        if self.mode == "naive":
            return gt_pm
        scale_bound = abs(self.s) if "scale" in self.lv else (
            max(abs(x) for x in self.lv["scale_range"]) if "scale_range" in self.lv else 0.0)
        return max(gt_pm, self.p90(p, kind) + scale_bound * float(np.linalg.norm(np.asarray(p) - self.m)))

    def move2(self, p, kind: str) -> np.ndarray:
        p = np.asarray(p, float)
        q = self.m + (1 + self.s) * (p - self.m)
        sig = self.p90(p, kind) / P90_2D
        if sig > 0:
            if "drift" in self.lv:
                q = q + sig * (0.7071 * self.common + 0.7071 * self.rng.normal(size=2))
            else:
                q = q + sig * self.rng.normal(size=2)
        return q

    def move1(self, x: float, p, kind: str) -> float:
        sig = self.p90(p, kind) / P90_1D
        return x + (sig * float(self.rng.normal()) if sig > 0 else 0.0)


def perturb(d0: dict, level: dict, seed: int, mode: str) -> dict:
    d = copy.deepcopy(d0)
    rng = np.random.default_rng(seed)
    P = Perturber(d, level, rng, mode)
    old = {w["id"]: (np.array(w["a"], float), np.array(w["b"], float)) for w in d["walls"]}
    # 1. vertices: shared by joints, moved once
    vid = {}
    for j in d.get("joints", []):
        key = (j["walls"][0], j["ends"][0])
        vid[(j["walls"][1], j["ends"][1])] = key
        vid[key] = key
    moved = {}
    for w in d["walls"]:
        for e in ("a", "b"):
            k = vid.get((w["id"], e), (w["id"], e))
            if k not in moved:
                moved[k] = P.move2(old[w["id"]][0 if e == "a" else 1], "wall")
    new = {}
    for w in d["walls"]:
        a = moved[vid.get((w["id"], "a"), (w["id"], "a"))]
        b = moved[vid.get((w["id"], "b"), (w["id"], "b"))]
        new[w["id"]] = (a, b)
        oa, ob = old[w["id"]]
        far = max(np.linalg.norm(oa - P.m), np.linalg.norm(ob - P.m))
        w["a"], w["b"] = a.round(4).tolist(), b.round(4).tolist()
        w["plus_minus_ft"] = P.pm_told(oa if np.linalg.norm(oa - P.m) > np.linalg.norm(ob - P.m) else ob,
                                       "wall", w["plus_minus_ft"])
        w["length_ft"] = float(np.linalg.norm(b - a))
        _ = far

    def frames(ab):
        a, b = ab
        L = float(np.linalg.norm(b - a))
        al = (b - a) / L
        return a, al, L

    fo = {k: frames(v) for k, v in old.items()}
    fn = {k: frames(v) for k, v in new.items()}
    outward = {w["id"]: np.array(w["outward"], float) for w in d["walls"]}

    def nrm(al, ref):
        n = np.array([-al[1], al[0]])
        return n if n @ ref > 0 else -n

    def nearest_wall(p):
        best, bw = math.inf, None
        for k, (a, al, L) in fo.items():
            t = np.clip((p - a) @ al, 0, L)
            dd = np.linalg.norm(p - (a + al * t))
            if dd < best:
                best, bw = dd, k
        return bw

    def carry(p, wid=None):
        p = np.asarray(p, float)
        wid = wid or nearest_wall(p)
        a, al, L = fo[wid]
        u, o = (p - a) @ al, (p - a) @ nrm(al, outward[wid])
        a2, al2, L2 = fn[wid]
        return a2 + al2 * u * (L2 / L) + nrm(al2, outward[wid]) * o

    def carry_poly(coords, jitter_kind=None):
        pts = [carry(c) for c in coords]
        if jitter_kind:
            c0 = np.mean(np.asarray(coords, float), axis=0)
            shift = P.move2(c0, jitter_kind) - (P.m + (1 + P.s) * (c0 - P.m))
            pts = [p + shift for p in pts]
        return [np.round(p, 4).tolist() for p in pts]

    # 2. meter
    mw = d["meter"]["wall_id"]
    a, al, L = fo[mw]
    mp = np.array(d["meter"]["pos"], float)
    mu = (mp[[0, 2]] - a) @ al
    mu2 = P.move1(mu, mp[[0, 2]], "edge")
    a2, al2, L2 = fn[mw]
    q = a2 + al2 * mu2 * (L2 / L)
    d["meter"]["pos"] = [float(q[0]), mp[1], float(q[1])]
    d["meter"]["plus_minus_ft"] = P.pm_told(mp[[0, 2]], "edge", d["meter"]["plus_minus_ft"])
    # 3. objects
    for o in d["objects"]:
        c = np.mean(np.asarray(o["footprint"], float), axis=0)
        if o.get("wall_id") and o.get("span_u"):
            wid = o["wall_id"]
            a, al, L = fo[wid]
            a2, al2, L2 = fn[wid]
            k = L2 / L
            u0, u1 = (P.move1(u * k, c, "edge") for u in o["span_u"])
            u0, u1 = min(u0, u1), max(u0, u1)
            o["span_u"] = [u0, u1]
            o["footprint"] = [np.round(a2 + al2 * u0, 4).tolist(), np.round(a2 + al2 * u1, 4).tolist()]
            o["y0"] = P.move1(o["y0"] * (1 + P.s), c, "edge")
            o["y1"] = P.move1(o["y1"] * (1 + P.s), c, "edge")
        else:
            o["footprint"] = carry_poly(o["footprint"], "edge")
        o["plus_minus_ft"] = P.pm_told(c, "edge", o.get("plus_minus_ft", 0.1))
    for key in ("obstacles", "overheads"):
        for o in d.get(key, []):
            c = np.mean(np.asarray(o["footprint"], float), axis=0)
            o["footprint"] = carry_poly(o["footprint"], "edge")
            o["y0"] *= 1 + P.s
            o["y1"] *= 1 + P.s
            o["plus_minus_ft"] = P.pm_told(c, "edge", o.get("plus_minus_ft", 0.1))
    # 4. coverage, interior
    for w in d["walls"]:
        k = fn[w["id"]][2] / fo[w["id"]][2]
        d["coverage"]["wall"][w["id"]] = [[x * k, y * k] for x, y in d["coverage"]["wall"].get(w["id"], [])]
    for key in ("ground", "overhead"):
        g = d["coverage"].get(key)
        if g:
            d["coverage"][key] = carry_geojson(g, carry)
    d["interior"] = [carry_poly(p) for p in d.get("interior", [])]
    return d


def carry_geojson(g: dict, carry) -> dict:
    def ring(r):
        return [np.round(carry(p), 4).tolist() for p in r]

    if g["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": [ring(r) for r in g["coordinates"]]}
    if g["type"] == "MultiPolygon":
        return {"type": "MultiPolygon", "coordinates": [[ring(r) for r in poly] for poly in g["coordinates"]]}
    return g


def summarize(r: dict) -> dict:
    b = r["best"]
    return {"decision": r["decision"], "best": None if b is None else {"wall": b["wall"], "u0": b["u"][0],
            "outcome": b["outcome"], "route_ft": b["route_ft"]}, "stats": r["stats"]}


class JobTimeout(Exception):
    pass


def _alarm(*_):
    raise JobTimeout()


def job(args):
    """One run, bounded: degenerate geometry from large perturbations can make shapely very slow
    or memory-hungry, so a run gets 90 s and 3 GB and is recorded as an error beyond that."""
    signal.signal(signal.SIGALRM, _alarm)
    resource.setrlimit(resource.RLIMIT_AS, (3 << 30, 3 << 30))
    signal.alarm(90)
    try:
        return _job(args)
    except Exception as e:  # noqa: BLE001  (recorded per run; counted in the summary)
        path, pname, level, seed, mode, _ = args
        return {"decision": "error", "error": f"{type(e).__name__}: {str(e)[:120]}", "scene": Path(path).stem, "profile": pname,
                "level": level["name"] if level else "gt", "seed": seed, "mode": mode}
    finally:
        signal.alarm(0)


def _job(args):
    path, pname, level, seed, mode, step = args
    d0 = json.loads(Path(path).read_text())
    d = perturb(d0, level, seed, mode) if level else d0
    t = time.time()
    r = E.decide(E.load_scene(d), prof(pname), step=step)
    out = summarize(r)
    b = r["best"]
    if r["decision"] == "pass" and b is not None:
        # the spot the perturbed capture would approve, checked against the laser ground truth
        gt = E.load_scene(d0)
        k = gt.walls[b["wall"]].L / E.load_scene(d).walls[b["wall"]].L
        u0 = min(max(0.0, b["u"][0] * k), gt.walls[b["wall"]].L - prof(pname)["battery"]["width_ft"])
        c = E.Solver3D(gt, prof(pname)).evaluate(gt.walls[b["wall"]], u0)
        out["gt_spot"] = {"u0": round(u0, 3), "outcome": c.outcome, "failing": c.failing(), "unsure": c.unsure()}
    out.update({"scene": d0["id"], "profile": pname, "level": level["name"] if level else "gt",
                "seed": seed, "mode": mode, "runtime_s": round(time.time() - t, 2)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", nargs="+", default=[
        "outputs/scenes/facade-M1.json", "outputs/scenes/facade-M3.json",
        "outputs/scenes/delivery_area-D1.json", "outputs/scenes/delivery_area-D3.json"])
    ap.add_argument("--profiles", nargs="+", default=["public", "C", "strictest"])
    ap.add_argument("--trials", type=int, default=4)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--step", type=float, default=1 / 3)
    ap.add_argument("--modes", nargs="+", default=["informed", "naive"])
    ap.add_argument("--out", default="outputs/sweep/results.jsonl")
    ap.add_argument("--resume", action="store_true", help="skip jobs already in --out (results are in job order)")
    args = ap.parse_args()
    jobs = [(s, p, None, 0, "gt", args.step) for s in args.scenes for p in args.profiles]
    for s in args.scenes:
        for p in args.profiles:
            for lv in LEVELS:
                for mode in args.modes:
                    if mode == "naive" and lv["name"] == "uniform_0in":
                        continue
                    for t in range(args.trials):
                        jobs.append((s, p, lv, 1000 * t + 17, mode, args.step))
    out = HERE / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    done = sum(1 for _ in open(out)) if args.resume and out.exists() else 0
    jobs = jobs[done:]
    t0 = time.time()
    n = 0
    with open(out, "a" if done else "w") as f, ProcessPoolExecutor(args.workers) as ex:
        for r in ex.map(job, jobs, chunksize=4):
            f.write(json.dumps(r) + "\n")
            n += 1
            if n % 100 == 0:
                print(f"{n}/{len(jobs)} {time.time() - t0:.0f}s", flush=True)
    print(f"done {n} runs in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
