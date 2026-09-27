"""Reference extractor: metric, gravity-aligned points + cameras -> scene v0.

Optional for producers (a path may write its own scene), but using it lets Track A paths be
compared on reconstruction quality alone: every path that emits points and cameras gets the same
wall, ground, facing, overhead and coverage logic. Inputs are in a metric world W with +y up
(ARKit's world after scale correction). Steps:

1. ground: RANSAC plane near horizontal among the lowest points;
2. walls: sequential RANSAC lines in plan over points 0.1-2.5 m above the ground, split at gaps;
3. meter: the tapped / clicked meter pixel(s) ray-cast onto the nearest wall plane;
4. chain: from the meter's wall, walk right and left joining segments whose lines meet within
   `join_m` of both ends (corner = line intersection); free ends are `unexplored`;
5. frame: origin on the ground below the meter, z = meter wall outward, y up;
6. objects: 2D feature clicks (or detector boxes reduced to jamb/sill/head points) ray-cast onto
   the chain wall faces through each camera, averaged over views (PR #13's method);
7. facing / overheads: PR #13's scan profiles, in meters, on the reconstructed points;
8. coverage: rays from each camera to the points it observed (`tracks`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from server.eval.chain import Chain, ground_y
from server.eval.coverage import build_grid
from server.eval.frame import apply, canonical_from_world, mat4_to_list, ray_arkit, transform_pose
from server.eval.visibility import Camera

POINT_ROLES = ("left", "right", "bottom", "top")


@dataclass
class Segment:
    c: np.ndarray  # point on the line (plan)
    d: np.ndarray  # unit direction (plan)
    t0: float
    t1: float
    n_in: int
    rms: float
    idx: np.ndarray = field(repr=False)

    def end(self, k):
        return self.c + (self.t0 if k == 0 else self.t1) * self.d


def ransac_plane_up(X, up, rng, thr=0.05, iters=800, max_tilt_deg=12.0):
    best_k, best = -1, None
    cmin = np.cos(np.radians(max_tilt_deg))
    Xs = X[rng.integers(0, len(X), min(len(X), 60000))]
    for _ in range(iters):
        s = Xs[rng.integers(0, len(Xs), 3)]
        n = np.cross(s[1] - s[0], s[2] - s[0])
        nn = np.linalg.norm(n)
        if nn < 1e-9:
            continue
        n /= nn
        if abs(n @ up) < cmin:
            continue
        k = int((np.abs((Xs - s[0]) @ n) < thr).sum())
        if k > best_k:
            best_k, best = k, (n, s[0])
    n, p = best
    inl = np.abs((X - p) @ n) < thr
    c = X[inl].mean(0)
    n = np.linalg.svd(X[inl] - c, full_matrices=False)[2][-1]
    n = n if n @ up > 0 else -n
    rms = float(np.sqrt(np.mean(((X[inl] - c) @ n) ** 2)))
    return n, c, inl, rms


def sequential_lines(Q, rng, thr=0.08, min_pts=120, min_len=1.5, gap=1.0, max_lines=14, iters=500):
    """Plan points -> wall segments."""
    segs: list[Segment] = []
    pool = np.arange(len(Q))
    for _ in range(max_lines):
        if len(pool) < min_pts:
            break
        P = Q[pool]
        best_k, best = -1, None
        for _ in range(iters):
            a, b = P[rng.integers(0, len(P), 2)]
            d = b - a
            if np.linalg.norm(d) < 0.5:
                continue
            d /= np.linalg.norm(d)
            nrm = np.array([-d[1], d[0]])
            k = int((np.abs((P - a) @ nrm) < thr).sum())
            if k > best_k:
                best_k, best = k, (a, nrm)
        if best is None or best_k < min_pts:
            break
        inl = np.abs((P - best[0]) @ best[1]) < thr
        c = P[inl].mean(0)
        d = np.linalg.svd(P[inl] - c, full_matrices=False)[2][0]
        nrm = np.array([-d[1], d[0]])
        inl = np.abs((P - c) @ nrm) < thr
        t = (P[inl] - c) @ d
        order = np.argsort(t)
        ts, ids = t[order], pool[inl][order]
        cuts = np.flatnonzero(np.diff(ts) > gap) + 1
        for part_t, part_i in zip(np.split(ts, cuts), np.split(ids, cuts)):
            if len(part_t) < min_pts * 0.5:
                continue
            t0, t1 = np.percentile(part_t, 1), np.percentile(part_t, 99)
            if t1 - t0 < min_len:
                continue
            res = (Q[part_i] - c) @ nrm
            segs.append(Segment(c, d, float(t0), float(t1), len(part_i), float(np.sqrt(np.mean(res**2))), part_i))
        pool = pool[~inl]
    return segs


def triangulate(cams: list[Camera], uv: list[tuple[float, float]]) -> np.ndarray:
    """Least-squares closest point to the pixel rays of several cameras."""
    A, b = np.zeros((3, 3)), np.zeros(3)
    for c, (u, v) in zip(cams, uv):
        d = ray_arkit(c.pose, c.K, np.array([u]), np.array([v]))[0]
        P = np.eye(3) - np.outer(d, d)
        A += P
        b += P @ c.pose[:3, 3]
    return np.linalg.solve(A, b)


def high_share(s: Segment, h: np.ndarray, p: "ExtractParams") -> float:
    """Share of a segment's points above p.wall_high_m: real walls reach up, clutter does not."""
    return float(np.mean(h[s.idx] > p.wall_high_m)) if len(s.idx) else 0.0


def merge_collinear(segs: list[Segment], points_w: np.ndarray, p: "ExtractParams") -> list[Segment]:
    """Merge pieces of one wall: directions within merge_angle_deg, lines within merge_offset_m of
    each other, and a gap along the line of at most merge_gap_m. The merged segment is refitted."""
    segs = list(segs)
    changed = True
    while changed:
        changed = False
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                a, b = segs[i], segs[j]
                if np.degrees(np.arccos(min(1.0, abs(a.d @ b.d)))) > p.merge_angle_deg:
                    continue
                nrm = np.array([-a.d[1], a.d[0]])
                if abs((b.c - a.c) @ nrm) > p.merge_offset_m:
                    continue
                tb = sorted(((b.end(0) - a.c) @ a.d, (b.end(1) - a.c) @ a.d))
                gap = max(tb[0] - a.t1, a.t0 - tb[1], 0.0)
                if gap > p.merge_gap_m:
                    continue
                idx = np.concatenate([a.idx, b.idx])
                P = points_w[idx][:, [0, 2]]
                c = P.mean(0)
                d = np.linalg.svd(P - c, full_matrices=False)[2][0]
                d = d if d @ a.d >= 0 else -d
                tt = (P - c) @ d
                res = (P - c) @ np.array([-d[1], d[0]])
                segs[i] = Segment(c, d, float(np.percentile(tt, 1)), float(np.percentile(tt, 99)), len(idx),
                                  float(np.sqrt(np.mean(res ** 2))), idx)
                del segs[j]
                changed = True
                break
            if changed:
                break
    return segs


def intersect(c1, d1, c2, d2):
    A = np.array([d1, -d2]).T
    if abs(np.linalg.det(A)) < 1e-6:
        return None
    t = np.linalg.solve(A, c2 - c1)
    return c1 + t[0] * d1


@dataclass
class ExtractParams:
    join_m: float = 1.5
    min_corner_deg: float = 30.0
    wall_band_m: tuple = (0.1, 2.5)
    line_thr_m: float = 0.08
    min_wall_pts: int = 60
    max_lines: int = 30
    gap_m: float = 3.0  # doors and dark windows leave gaps this wide in sparse clouds
    fit_radius_m: float = 14.0  # walls are fitted only this far (plan) from the triangulated meter
    fit_voxel_m: float = 0.05  # wall-band points are thinned to one per voxel so dense clouds weigh like sparse ones
    merge_angle_deg: float = 5.0  # collinear pieces of one wall (split by clutter or holes) are merged
    merge_offset_m: float = 0.15
    merge_gap_m: float = 4.0
    wall_min_high_share: float = 0.15  # a chain wall needs this share of its points above wall_high_m
    wall_high_m: float = 1.5
    scale_rel_pm: float = 0.0  # producer's declared scale bound (e.g. 0.04 for 2-sigma of 2%)
    voxel_m: float = 0.1
    profile_min_count: int = 3
    seed: int = 7


def extract_scene(points_w: np.ndarray, tracks: list[np.ndarray], cams: list[Camera], meter_clicks: list,
                  features: list[dict], p: ExtractParams, provenance: dict, scene_id: str,
                  source_frame: str, T_w_from_source: np.ndarray | None = None) -> tuple[dict, dict]:
    """Returns (scene without assets/coverage grid, working state for the caller)."""
    rng = np.random.default_rng(p.seed)
    up = np.array([0.0, 1.0, 0.0])
    report: dict = {}

    # 1. ground
    cam_y = np.median([c.pose[1, 3] for c in cams])
    low = points_w[points_w[:, 1] < cam_y - 0.8]
    g_n, g_c, _, g_rms = ransac_plane_up(low, up, rng)
    h = (points_w - g_c) @ g_n
    report["ground"] = {"normal_tilt_deg": float(np.degrees(np.arccos(g_n @ up))), "rms_m": g_rms,
                        "points": int(len(low))}

    # 2. walls, near the meter (reach is 26 ft; far buildings would eat the RANSAC budget)
    band = (h > p.wall_band_m[0]) & (h < p.wall_band_m[1])
    if len(meter_clicks) >= 2:
        M0 = triangulate([cams[ci] for ci, _, _ in meter_clicks], [(u, v) for _, u, v in meter_clicks])
        band &= np.linalg.norm(points_w[:, [0, 2]] - M0[[0, 2]], axis=1) < p.fit_radius_m
        report["meter_triangulated_w"] = [round(float(v), 3) for v in M0]
    band_idx = np.flatnonzero(band)
    if p.fit_voxel_m:
        keys = np.floor(points_w[band_idx] / p.fit_voxel_m).astype(np.int64)
        _, first = np.unique(keys, axis=0, return_index=True)
        band_idx = band_idx[np.sort(first)]
    report["wall_band_points"] = {"raw": int(band.sum()), "thinned": int(len(band_idx))}
    Q = points_w[band_idx][:, [0, 2]]
    segs = sequential_lines(Q, rng, thr=p.line_thr_m, min_pts=p.min_wall_pts, gap=p.gap_m, max_lines=p.max_lines)
    for s in segs:
        s.idx = band_idx[s.idx]
    n_raw = len(segs)
    segs = merge_collinear(segs, points_w, p)
    report["segments"] = [{"len_m": s.t1 - s.t0, "points": s.n_in, "rms_m": s.rms,
                           "high_share": round(high_share(s, h, p), 3)} for s in segs]
    report["segments_merged"] = n_raw - len(segs)

    # 3. meter: ray-cast clicks onto segment planes
    def cast(cam: Camera, u, v, allow=None, face=None):
        o = cam.pose[:3, 3]
        d = ray_arkit(cam.pose, cam.K, np.array([u]), np.array([v]))[0]
        best = None
        for k, s in enumerate(segs):
            if allow is not None and k not in allow:
                continue
            nrm = np.array([-s.d[1], s.d[0]])
            off = 0.0 if face is None else face.get(k, 0.0)
            c3 = np.array([s.c[0], 0, s.c[1]]) + off * np.array([nrm[0], 0, nrm[1]])
            n3 = np.array([nrm[0], 0.0, nrm[1]])
            den = d @ n3
            if abs(den) < 1e-6:
                continue
            t = ((c3 - o) @ n3) / den
            if t <= 0.3:
                continue
            X = o + t * d
            tt = (X[[0, 2]] - s.c) @ s.d
            if s.t0 - 0.5 <= tt <= s.t1 + 0.5 and (best is None or t < best[0]):
                best = (t, k, X)
        return best

    hits = [cast(cams[ci], u, v) for ci, u, v in meter_clicks]
    hits = [x for x in hits if x is not None]
    if not hits:
        raise RuntimeError("meter clicks hit no wall")
    ks = [x[1] for x in hits]
    mk = max(set(ks), key=ks.count)
    M = np.mean([x[2] for x in hits if x[1] == mk], axis=0)
    report["meter_hits"] = len(hits)

    # orient the meter wall: outward toward the clicking cameras
    s0 = segs[mk]
    nrm = np.array([-s0.d[1], s0.d[0]])
    cam_side = np.mean([(cams[ci].pose[[0, 2], 3] - s0.c) @ nrm for ci, _, _ in meter_clicks])
    # outward = direction turned clockwise seen from above = (-dz, dx); flip d if cameras are behind
    if cam_side < 0:
        s0.d, s0.t0, s0.t1 = -s0.d, -s0.t1, -s0.t0

    # 4. chain
    used = {mk}
    chain = [[s0.end(0).copy(), s0.end(1).copy(), mk]]

    def extend(right: bool):
        while True:
            cur = chain[-1] if right else chain[0]
            a, b, k = cur
            e = b if right else a
            dcur = (b - a) / np.linalg.norm(b - a)
            best = None
            for j, s in enumerate(segs):
                if j in used:
                    continue
                X = intersect(e, dcur, s.c, s.d)
                if X is None:
                    continue
                ang = np.degrees(np.arccos(min(1.0, abs(dcur @ s.d))))
                if ang < p.min_corner_deg or high_share(s, h, p) < p.wall_min_high_share:
                    continue
                ends = [s.end(0), s.end(1)]
                de = [np.linalg.norm(X - q) for q in ends]
                near = int(np.argmin(de))
                if np.linalg.norm(X - e) < p.join_m and de[near] < p.join_m:
                    score = np.linalg.norm(X - e) + de[near]
                    if best is None or score < best[0]:
                        best = (score, j, X, ends[1 - near])
            if best is None:
                return
            _, j, X, far = best
            used.add(j)
            if right:
                cur[1] = X
                chain.append([X.copy(), far.copy(), j])
            else:
                cur[0] = X
                chain.insert(0, [far.copy(), X.copy(), j])

    extend(True)
    extend(False)

    # 5. canonical frame
    foot_y = g_c[1] - (g_n[0] * (M[0] - g_c[0]) + g_n[2] * (M[2] - g_c[2])) / g_n[1]
    foot = np.array([M[0], foot_y, M[2]])
    a, b, _ = next(c for c in chain if c[2] == mk)
    dd = (b - a) / np.linalg.norm(b - a)
    outward_w = np.array([-dd[1], 0.0, dd[0]])
    T = canonical_from_world(foot, up, outward_w)
    Tp = T[[0, 2]][:, [0, 2]]  # plan part (rotation only; no scale)
    plan = lambda q: (np.asarray(q) - foot[[0, 2]]) @ Tp.T  # noqa: E731

    n_c = T[:3, :3] @ g_n
    off_c = float(n_c @ apply(T, g_c))

    def height(Xc):
        Xc = np.atleast_2d(Xc)
        return Xc[:, 1] - (off_c - n_c[0] * Xc[:, 0] - n_c[2] * Xc[:, 2]) / n_c[1]

    walls = []
    mk_i = [c[2] for c in chain].index(mk)
    s_vals = [0.0] * len(chain)
    s_vals[mk_i] = float(plan(chain[mk_i][0])[0])  # the meter wall runs along +x from the origin
    for i in range(mk_i + 1, len(chain)):
        s_vals[i] = s_vals[i - 1] + float(np.linalg.norm(chain[i - 1][1] - chain[i - 1][0]))
    for i in range(mk_i - 1, -1, -1):
        s_vals[i] = s_vals[i + 1] - float(np.linalg.norm(chain[i][1] - chain[i][0]))
    pts_c = apply(T, points_w)
    for i, (a, b, k) in enumerate(chain):
        L = float(np.linalg.norm(b - a))
        s_mid = abs(s_vals[i] + L / 2)
        seg = segs[k]
        hh = (points_w[seg.idx] - g_c) @ g_n
        walls.append({
            "id": f"w{i + 1}", "role": "house",
            "baseline_m": [[round(float(v), 4) for v in plan(a)], [round(float(v), 4) for v in plan(b)]],
            "height_m": round(float(max(2.5, np.percentile(hh, 99))), 2),
            "s_range_m": [round(s_vals[i], 4), round(s_vals[i] + L, 4)],
            "plus_minus_m": round(max(0.03, 3 * seg.rms) + p.scale_rel_pm * (s_mid + L / 2), 3),
            "evidence": "observed",
            "support": {"points": int(seg.n_in), "fit_rms_m": round(seg.rms, 4)},
            "_seg": k,
            "_fit_pm": max(0.03, 3 * seg.rms),
        })
    report["chain"] = [(w["id"], w["s_range_m"]) for w in walls]

    # face offset per chain wall (the plinth stands proud of the brick on ETH3D facade)
    face = {}
    for w in walls:
        s = segs[w["_seg"]]
        nrm = np.array([-s.d[1], s.d[0]])
        q = points_w[:, [0, 2]]
        lat = (q - s.c) @ s.d
        out = (q - s.c) @ nrm
        k = (lat > s.t0 + 0.3) & (lat < s.t1 - 0.3) & (h > 1.2) & (h < 2.4) & (np.abs(out) < 0.3)
        if k.sum() > 30:
            hist, edges = np.histogram(out[k], bins=np.arange(-0.3, 0.3001, 0.01))
            face[w["_seg"]] = float(edges[hist.argmax()] + 0.005)
    report["face_offset_m"] = {wall_by_k["id"]: face.get(wall_by_k["_seg"]) for wall_by_k in walls}

    # 6. objects
    cams_c = [Camera(c.id, transform_pose(T, c.pose), c.K, c.w, c.h) for c in cams]
    wall_by_seg = {w["_seg"]: w for w in walls}
    chain_segs = set(wall_by_seg)
    objects = [{
        "id": "meter", "class": "meter",
        "geometry": {"kind": "point", "pos_m": [round(float(v), 4) for v in apply(T, M)]},
        "wall_id": wall_by_seg[mk]["id"], "confidence": 1.0,
        "plus_minus_m": round(max(0.03, 3 * segs[mk].rms), 3),
        "source": {"kind": "tap", "views": [{"image": cams[ci].id, "points_px": [[float(u), float(v)]]} for ci, u, v in meter_clicks]},
        "evidence": "observed",
    }]
    for f in features:
        per = []
        views = []
        for ci, pts in f["points_px"].items():
            vals, kk = [], None
            for r in POINT_ROLES:
                hit = cast(cams[ci], *pts[r], allow=chain_segs, face=face)
                if hit is None:
                    break
                vals.append(hit)
            if len(vals) < 4:
                continue
            ks_ = [v[1] for v in vals]
            kk = max(set(ks_), key=ks_.count)
            w = wall_by_seg[kk]
            Xc = apply(T, np.array([v[2] for v in vals]))
            (x0, z0), (x1, z1) = w["baseline_m"]
            along = np.array([x1 - x0, z1 - z0]) / np.hypot(x1 - x0, z1 - z0)
            s_ = w["s_range_m"][0] + (Xc[:, [0, 2]] - np.array([x0, z0])) @ along
            hh_ = height(Xc)
            per.append((w["id"], np.array([s_[0], s_[1], hh_[2], hh_[3]])))
            views.append({"image": cams[ci].id, "points_px": [list(map(float, pts[r])) for r in POINT_ROLES]})
        if not per:
            continue
        wid = max({x[0] for x in per}, key=[x[0] for x in per].count)
        vals = np.array([x[1] for x in per if x[0] == wid])
        m = vals.mean(0)
        spread = float(np.max(vals.max(0) - vals.min(0))) if len(vals) > 1 else 0.15
        wall = next(w for w in walls if w["id"] == wid)
        pm = max(0.03, spread) + wall["_fit_pm"] + p.scale_rel_pm * max(abs(m[0]), abs(m[1]))
        objects.append({
            "id": f["id"], "class": f["class"], "label": f.get("label", f["class"]),
            "geometry": {"kind": "wall_rect", "wall_id": wid, "s_m": [round(float(m[0]), 4), round(float(m[1]), 4)],
                         "bottom_m": round(float(max(0.0, m[2])), 4), "top_m": round(float(m[3]), 4)},
            "wall_id": wid, "confidence": 1.0, "plus_minus_m": round(pm, 3),
            "source": {"kind": "annotation", "model": f.get("source_model", "hand clicks (PR #13 annotations.json)"), "views": views},
            **({"attrs": f["attrs"]} if "attrs" in f else {}),
            "evidence": "observed",
        })

    scene = {
        "format": "base-scene/v0",
        "scene_id": scene_id,
        "provenance": provenance,
        "frame": {"units": "m", "convention": "meter_anchored/v0",
                  "anchor": {"element_id": "meter", "plus_minus_m": objects[0]["plus_minus_m"]},
                  "source_frame": source_frame,
                  "from_source": mat4_to_list(T if T_w_from_source is None else T @ T_w_from_source, 9)},
        "walls": [{k: v for k, v in w.items() if not k.startswith("_")} for w in walls],
        "chain": {"wall_ids": [w["id"] for w in walls],
                  "corners": [{"id": f"c{i + 1}", "left_wall": walls[i]["id"], "right_wall": walls[i + 1]["id"],
                               "point_m": walls[i]["baseline_m"][1], "s_m": walls[i]["s_range_m"][1],
                               "evidence": "inferred"} for i in range(len(walls) - 1)],
                  "ends": {"left": {"kind": "unexplored", "point_m": walls[0]["baseline_m"][0], "s_m": walls[0]["s_range_m"][0]},
                           "right": {"kind": "unexplored", "point_m": walls[-1]["baseline_m"][1], "s_m": walls[-1]["s_range_m"][1]}}},
        "ground": {"surface": {"kind": "plane", "normal": [round(float(v), 6) for v in n_c],
                               "offset_m": round(off_c, 5)},
                   "plus_minus_m": round(max(0.03, 3 * g_rms), 3), "regions": []},
        "objects": objects,
        "coverage": {"method": "rays_to_triangulated_points"},
        "keyframes": [{"id": c.id, "pose": mat4_to_list(c.pose), "intrinsics": [float(v) for v in c.K],
                       "w": c.w, "h": c.h, "pose_source": "sfm_scaled_by_arkit"} for c in cams_c],
    }
    for c in scene["chain"]["corners"]:
        i = [w["id"] for w in walls].index(c["left_wall"])
        (a0, b0), (a1, b1) = np.asarray(walls[i]["baseline_m"]), np.asarray(walls[i + 1]["baseline_m"])
        d0, d1 = (b0 - a0) / np.linalg.norm(b0 - a0), (b1 - a1) / np.linalg.norm(b1 - a1)
        c["turn_deg"] = round(float(np.degrees(np.arctan2(d0[0] * d1[1] - d0[1] * d1[0], d0 @ d1))), 2)
        c["plus_minus_m"] = max(walls[i]["plus_minus_m"], walls[i + 1]["plus_minus_m"])
    state = {"T": T, "points_c": pts_c, "cams_c": cams_c, "report": report}
    return scene, state


def profiles(points: np.ndarray, scene: dict, min_count: int = 5, bin_m: float = 0.3048,
             over_out: float = 0.91, over_max: float = 3.66, over_min: float = 1.22,
             relief: float = 0.3048, facing_max: float = 18.29) -> tuple[list, list]:
    """PR #13's scan_profiles in meters, on any point set in the scene frame. Per bin along each
    chain wall: the lowest overhead (a cell within `over_out` of the wall whose lowest point is
    above `over_min`) and the facing depth (nearest point more than `relief` out, 0.3-1.83 m high)."""
    ch = Chain(scene)
    plan = points[:, [0, 2]]
    h = points[:, 1] - ground_y(scene, plan)
    over, facing = [], []
    for w in ch.walls:
        lat = (plan - w.p0) @ w.along
        out = (plan - w.p0) @ w.outward
        near = (out > 0.09) & (out < over_out) & (h > 0.15) & (h < over_max) & (lat > 0) & (lat < w.length)
        cell = np.floor(np.c_[lat[near], out[near]] / 0.0762).astype(int)
        cell_min, cell_n = {}, {}
        for c, hh in zip(map(tuple, cell), h[near]):
            cell_min[c] = min(cell_min.get(c, np.inf), hh)
            cell_n[c] = cell_n.get(c, 0) + 1
        for a in np.arange(0.0, w.length, bin_m):
            b = min(a + bin_m, w.length)
            k = (lat >= a + 0.05) & (lat < b - 0.05)
            lows = [v for c, v in cell_min.items() if a <= (c[0] + 0.5) * 0.0762 < b and v > over_min and cell_n[c] >= min_count]
            over.append((w.id, w.s0 + a, w.s0 + b, float(min(lows)) if lows else None))
            kf = k & (out > relief) & (out < facing_max) & (h > 0.3) & (h < 1.83)
            if kf.sum() >= max(3, min_count * 4):
                depth, lb = float(np.sort(out[kf])[min(2, kf.sum() - 1)]), False
            elif (k & (np.abs(h) < 0.15) & (out > facing_max - 1.5) & (out < facing_max)).sum() >= min_count * 4:
                depth, lb = facing_max, True
            else:
                depth, lb = None, False
            facing.append((w.id, w.s0 + a, w.s0 + b, depth, lb))
    return over, facing


def merge_profile_runs(rows, key, with_lb=False):
    out = []
    for r in rows:
        wid, a, b, v = r[:4]
        lb = r[4] if with_lb else False
        if v is None:
            continue
        if out and out[-1][0] == wid and abs(out[-1][2] - a) < 1e-6 and key(out[-1][3]) == key(v) and out[-1][4] == lb:
            out[-1] = (wid, out[-1][1], b, min(out[-1][3], v), lb)
        else:
            out.append((wid, a, b, v, lb))
    return out


def profile_entries(points: np.ndarray, scene: dict, pm_m: float, min_count: int = 5) -> tuple[list, list]:
    over, facing = profiles(points, scene, min_count=min_count)
    o_runs = merge_profile_runs(over, key=lambda v: round(v / 0.3048))
    f_runs = merge_profile_runs(facing, key=lambda v: min(int(v / 0.6096), 10), with_lb=True)
    overheads = [{"wall_id": w, "s_m": [round(a, 3), round(b, 3)], "clearance_m": round(v, 3),
                  "plus_minus_m": pm_m, "evidence": "observed"} for w, a, b, v, _ in o_runs]
    facing_e = [{"wall_id": w, "s_m": [round(a, 3), round(b, 3)], "depth_m": round(v, 3),
                 "plus_minus_m": pm_m, "evidence": "observed", **({"lower_bound": True} if lb else {})}
                for w, a, b, v, lb in f_runs]
    return overheads, facing_e


def coverage_from_tracks(points_c: np.ndarray, tracks: list[np.ndarray], cams_c: list[Camera], lo, hi, voxel):
    """Rays from each camera to the points it observed."""
    by_cam: dict[int, list[int]] = {}
    for pi, tr in enumerate(tracks):
        for ci in tr:
            by_cam.setdefault(int(ci), []).append(pi)
    sets = ((cams_c[ci].pose[:3, 3], points_c[np.array(pis)], None) for ci, pis in sorted(by_cam.items()))
    return build_grid(sets, lo, hi, voxel)
