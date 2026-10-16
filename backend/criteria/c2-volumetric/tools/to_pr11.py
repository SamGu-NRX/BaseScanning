"""Convert a C2 labelled scene plus its occupancy grid into a PR #11 scene.json (feet, ARKit-like).

    python to_pr11.py SCENE.json GRID.npz OUT.json [--schema scene.schema.json]

Walls are the scene's `reference_chain` (scan-frame segments, ordered so the outward side is the
direction turned clockwise seen from above). Openings and wall equipment become spans on their
nearest wall; things standing off the wall (AC, gas meter) also get a plan footprint. Overheads,
facing depths and the observed coverage bands are measured from the same grid C2 uses, so both
engines see the same evidence and differences come from the logic.

Frame: scene x = scan X, scene z = -scan Y, scene y = height above the ground at the meter, all
relative to the meter's ground point, in feet.
"""

import argparse
import json
import sys
from pathlib import Path

import jsonschema
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from c2vol.grid import OCCUPIED, UNKNOWN, Grid  # noqa: E402
from c2vol.sweep import Maps, effective_state, sample_states  # noqa: E402

FT = 0.3048
PR11_TYPES = {"window", "door", "garage_door", "ac", "gas_meter", "elec_box", "vent", "downspout", "pool"}
OFF_WALL = {"ac", "gas_meter", "pool"}
GROUND_TYPES = {"drive", "concrete", "gravel", "lawn", "mulch", "deck"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene")
    ap.add_argument("grid")
    ap.add_argument("out")
    ap.add_argument("--schema")
    ap.add_argument("--step-ft", type=float, default=1.0)
    a = ap.parse_args()
    sc = json.loads(Path(a.scene).read_text())
    g0 = Grid.load(a.grid)
    g = Grid(g0.origin, g0.res, effective_state(g0.state)[0])
    M = Maps(g)
    chain = sc["reference_chain"]["walls"]
    mp = np.array(sc["meter"]["pos"])

    def terrain_at(xy):
        ij = np.floor((np.asarray(xy) - g.origin[:2]) / g.res).astype(int)
        ij = np.clip(ij, 0, np.array(M.terrain.shape) - 1)
        return float(M.terrain[ij[0], ij[1]])

    z0 = terrain_at(mp[:2])

    def to_scene(X):
        X = np.asarray(X, float)
        return [(X[0] - mp[0]) / FT, (-(X[1] - mp[1])) / FT]

    walls = []
    for w in chain:
        A, B = np.array(w["a"][:2]), np.array(w["b"][:2])
        d = (B - A) / np.linalg.norm(B - A)
        out = np.array([d[1], -d[0]])
        mid = (A + B) / 2
        st_in = sample_states(g, np.array([[*(mid - 0.3 * out), terrain_at(mid) + 0.8]]))[0]
        st_out = sample_states(g, np.array([[*(mid + 0.5 * out), terrain_at(mid) + 0.8]]))[0]
        walls.append({"id": w["id"], "A": A, "B": B, "d": d, "out": out, "L": float(np.linalg.norm(B - A)),
                      "check": (int(st_in), int(st_out))})
    # s: 0 at the meter's projection onto its wall, cumulative along the chain
    cum = np.cumsum([0.0] + [w["L"] for w in walls])

    def proj(w, xy):
        t = float(np.dot(np.asarray(xy) - w["A"], w["d"]))
        return t, float(np.dot(np.asarray(xy) - w["A"], w["out"]))

    def nearest_wall(xy):
        best = None
        for i, w in enumerate(walls):
            t, o = proj(w, xy)
            tc = min(max(t, 0), w["L"])
            dist = np.hypot(t - tc, o)
            if best is None or dist < best[0]:
                best = (dist, i, t)
        return best

    _, mi, mt = nearest_wall(mp[:2])
    s_meter = cum[mi] + mt

    def s_of(i, t):
        return (cum[i] + t - s_meter) / FT

    objects = []
    for o in sc.get("objects", []):
        typ = o["type"]
        if typ not in PR11_TYPES:
            continue
        P = np.array(o["plan_polygon"])
        _, i, _ = nearest_wall(P.mean(0))
        ts = [proj(walls[i], p)[0] for p in P]
        t0, t1 = max(min(ts), 0.0), min(max(ts), walls[i]["L"])
        gz = terrain_at(P.mean(0))
        ob = {"type": typ, "wall_id": walls[i]["id"], "span_ft": [round(s_of(i, t0), 3), round(s_of(i, t1), 3)],
              "bottom_ft": round(max(0.0, (o["z_range"][0] - gz) / FT), 3),
              "top_ft": round(max(0.0, (o["z_range"][1] - gz) / FT), 3),
              "source": "tap", "plus_minus_ft": round(o.get("plus_minus_m", 0.1) / FT, 3)}
        if typ in OFF_WALL:
            ob["footprint"] = [[round(v, 3) for v in to_scene(p)] for p in P]
        objects.append(ob)

    ground = [{"type": gt["type"], "polygon": [[round(v, 3) for v in to_scene(p)] for p in gt["plan_polygon"]]}
              for gt in sc.get("ground_types", []) if gt["type"] in GROUND_TYPES]

    # overheads, facing, coverage per step along each wall
    step = a.step_ft * FT
    over_rows, face_rows, cov = [], [], {"wall": [], "ground": [], "overhead": [], "facing": []}
    for i, w in enumerate(walls):
        for t in np.arange(step / 2, w["L"], step):
            base = w["A"] + t * w["d"]
            gz = terrain_at(base + 0.3 * w["out"])
            s_a, s_b = s_of(i, t - step / 2), s_of(i, t + step / 2)
            # overhead: lowest occupied 4-12 ft above ground, 0.3-3 ft out
            outs = np.arange(0.3, 3.0, 0.3) * FT
            hs = np.arange(4.0, 12.0, 0.1) * FT
            O, Hh = np.meshgrid(outs, hs, indexing="ij")
            X = np.stack([base[0] + O * w["out"][0], base[1] + O * w["out"][1], gz + Hh], -1)
            st = sample_states(g, X)
            # an overhead has clear space under it: skip probe columns occupied at 1-4 ft (a pillar
            # or wall face standing proud of the baseline is not an overhead)
            low = np.stack([base[0] + outs * w["out"][0], base[1] + outs * w["out"][1]], -1)
            L = np.concatenate([np.repeat(low, 13, 0), np.tile(gz + np.linspace(1.0, 3.9, 13) * FT, len(outs))[:, None]], 1)
            blocked = (sample_states(g, L) == OCCUPIED).reshape(len(outs), 13).any(1)
            occ_h = (st[~blocked] == OCCUPIED).any(0) if (~blocked).any() else np.zeros(len(hs), bool)
            if occ_h.any():
                over_rows.append((w["id"], s_a, s_b, round(hs[np.argmax(occ_h)] / FT, 1)))
            # facing: first occupied straight out at 1-6 ft height, up to 60 ft
            ds = np.arange(0.35, 60 * FT, g.res)
            hh = np.arange(1.0, 6.0, 0.25) * FT
            D, Hh = np.meshgrid(ds, hh, indexing="ij")
            X = np.stack([base[0] + D * w["out"][0], base[1] + D * w["out"][1], gz + Hh], -1)
            st = sample_states(g, X)
            hit = np.nonzero((st == OCCUPIED).sum(1) >= 2)[0]
            depth = ds[hit[0]] if len(hit) else 60 * FT
            face_rows.append((w["id"], s_a, s_b, depth / FT))
            # coverage: wall face (probes 0-0.3 ft in front, 0.5-6.5 ft up)
            probes = np.array([[*(base + o * w["out"]), gz + h] for o in (0.05, 0.1) for h in np.arange(0.5, 6.5, 0.25) * FT])
            wall_ok = (sample_states(g, probes) != UNKNOWN).mean() >= 0.8
            gpro = [base + o * w["out"] for o in np.arange(0.3, 6.0, 0.25) * FT]
            gseen = np.array([M.ground_seen[tuple(np.clip(np.floor((p - g.origin[:2]) / g.res).astype(int), 0,
                                                          np.array(M.terrain.shape) - 1))] for p in gpro])
            ground_ok = gseen.mean() >= 0.8
            op = np.array([[*(base + o * w["out"]), gz + h] for o in np.arange(0.3, 3.0, 0.5) * FT
                           for h in np.arange(3.3, 6.5, 0.25) * FT])
            over_ok = (sample_states(g, op) != UNKNOWN).mean() >= 0.8
            fp = np.array([[*(base + o * w["out"]), gz + 3 * FT] for o in np.arange(0.35, min(depth, 20 * FT), 0.1)])
            face_ok = len(fp) == 0 or (sample_states(g, fp) != UNKNOWN).mean() >= 0.9
            for band, ok in (("wall", wall_ok), ("ground", ground_ok), ("overhead", over_ok), ("facing", face_ok)):
                if ok:
                    cov[band].append((s_a, s_b))

    def merge(rows):
        out = []
        for r in rows:
            if out and abs(out[-1][1] - r[0]) < 0.05:
                out[-1][1] = r[1]
            else:
                out.append([r[0], r[1]])
        return out

    def merge_keyed(rows, key):
        out = []
        for wid, sa, sb, v in rows:
            k = key(v)
            if out and out[-1][0] == wid and abs(out[-1][2] - sa) < 0.05 and key(out[-1][3]) == k:
                out[-1][2] = sb
                out[-1][3] = min(out[-1][3], v)
            else:
                out.append([wid, sa, sb, v])
        return out

    overheads = [{"wall_id": wid, "span_ft": [round(a_, 2), round(b_, 2)], "clearance_ft": v, "plus_minus_ft": 0.1}
                 for wid, a_, b_, v in merge_keyed(over_rows, lambda v: round(v))]
    facing = [{"wall_id": wid, "span_ft": [round(a_, 2), round(b_, 2)], "depth_ft": round(v, 2), "plus_minus_ft": 0.1}
              for wid, a_, b_, v in merge_keyed(face_rows, lambda v: min(int(v // 2), 10))]
    observed = []
    for band, rows in cov.items():
        for sa, sb in merge(rows):
            e = {"band": band, "span_ft": [round(sa, 2), round(sb, 2)]}
            if band == "ground":
                e["out_ft"] = 6.0
            observed.append(e)

    scene = {
        "schema_version": "1.0",
        "meter": {"pos": [0.0, round((mp[2] - z0) / FT, 3), 0.0], "wall_id": walls[mi]["id"],
                  "plus_minus_ft": round(sc["meter"].get("plus_minus_m", 0.09) / FT, 3)},
        "walls": [{"id": w["id"], "baseline": [[round(v, 3) for v in to_scene(w["A"])],
                                               [round(v, 3) for v in to_scene(w["B"])]],
                   "plus_minus_ft": 0.1} for w in walls],
        "objects": objects,
        "ground": ground,
        "overheads": overheads,
        "facing": facing,
        "coverage": {"ends": sc["reference_chain"].get("ends", {}), "observed": observed},
    }
    if a.schema:
        jsonschema.validate(scene, json.loads(Path(a.schema).read_text()))
    Path(a.out).write_text(json.dumps(scene, indent=1))
    print("walls", [(w["id"], round(w["L"] / FT, 1), w["check"]) for w in walls],
          "objects", len(objects))
    for b, r in cov.items():
        print(" observed", b, [[round(x, 1) for x in m] for m in merge(r)])


if __name__ == "__main__":
    main()
