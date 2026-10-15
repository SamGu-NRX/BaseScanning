"""Build a C1 3D scene (scene3d JSON) from an ETH3D laser scan, COLMAP poses and an annotation file.

Reuses PR #13's builder (house-scanning t3/verification,
verification/scenes/eth3d-facade/build_scene.py): scan reading, ground RANSAC, the ARKit-like
scene frame (y = ground normal, feet), COLMAP -> ARKit camera conversion and clicked-feature
back-projection. Adds what the 3D rules need and PR #13's single-chain scene.json cannot hold:
- a wall graph with joints classified inside / outside from the outward normals;
- a ground height grid (0.5 ft) from the scan, so slope, steps and ground extent are measured;
- overheads (scan cells with open space below 4 ft and structure above) and standing obstacles
  (scan points 0.3-6.5 ft above ground, more than 0.4 ft off any wall face) as plan polygons;
- coverage per band from every camera with a depth test against the scan (PR #12's occlusion
  finding), instead of a 75 degree frustum test alone and an asserted 6 ft of ground.

    python build_scene3d.py annotations/facade.json [--cameras pr13|all]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage
from shapely import Polygon, box, unary_union
from shapely.geometry import mapping

HERE = Path(__file__).resolve().parent
PR13 = Path("/home/ubuntu/work/verif/verification/scenes/eth3d-facade")
sys.path.insert(0, str(PR13))
import build_scene as pr13  # noqa: E402
import scantools  # noqa: E402

M_PER_FT = 0.3048
ETH3D = Path("/home/ubuntu/eth3d")
OUT = HERE / "outputs" / "scenes"
GRID_FT = 0.5
MAX_VIEW_ANGLE_DEG = 75.0  # PR #13's wall-band limit, kept
# Ground counts as seen when one full-resolution pixel covers at most 3 in along the viewing ray
# (enough to tell the surface type and a 3 in step), up to 88 degrees from vertical. A fixed 82 degree
# cutoff threw away the whole wall base, which the DSLR photos resolve at about 1 in.
GROUND_MAX_INCIDENCE_DEG = 88.0
GROUND_GSD_MAX_FT = 0.25
KF_SCALE = 0.25
MIN_VIEWS = 1
ZBUF_SCALE = 1 / 16


def fit_wall(plan, h, a, b, rng, trim=1.0):
    d = (b - a) / np.linalg.norm(b - a)
    nrm = np.array([-d[1], d[0]])
    t = (plan - a) @ d
    L = np.linalg.norm(b - a)
    m = (np.abs((plan - a) @ nrm) < 1.0) & (t > trim) & (t < L - trim) & (h > 0.3) & (h < 8.0)
    if m.sum() < 300:
        raise SystemExit(f"only {m.sum()} scan points near rough wall {a}->{b}")
    c, dd, inl = pr13.ransac_line2d(plan[m], 0.1, rng)
    if dd @ d < 0:
        dd = -dd
    res = (plan[m][inl] - c) @ np.array([-dd[1], dd[0]])
    return c, dd, float(np.sqrt(np.mean(res**2))), int(inl.sum())


def extent_end(plan, h, c, d, rough_end, side):
    nrm = np.array([-d[1], d[0]])
    t_rough = (rough_end - c) @ d
    m = (np.abs((plan - c) @ nrm) < 0.15) & (h > 0.3) & (h < 8.0) & (np.abs((plan - c) @ d - t_rough) < 4)
    t = (plan[m] - c) @ d
    te = np.percentile(t, 99.8) if side == 1 else np.percentile(t, 0.2)
    return c + te * d


def camera_zbuffers(kfs, P):
    """Depth image per camera from scan points (ARKit convention, scene ft)."""
    zb = []
    for k in kfs:
        w, h = max(1, int(k.w * ZBUF_SCALE)), max(1, int(k.h * ZBUF_SCALE))
        u, v, z = k.project(P)
        s = w / k.w
        ok = (z > 0.3) & (u >= 0) & (u < k.w) & (v >= 0) & (v < k.h)
        iu = np.clip((u[ok] * s).astype(int), 0, w - 1)
        iv = np.clip((v[ok] * s).astype(int), 0, h - 1)
        buf = np.full((h, w), np.inf)
        np.minimum.at(buf, (iv, iu), z[ok])
        zb.append((buf, s))
    return zb


def visible_any(kfs, zb, X, normal=None, max_angle=None, tol_ft=0.6, gsd_max_ft=None):
    """For probe points X (n, 3): seen by at least one camera (in frame, in front, not occluded by
    scan geometry, within max_angle of `normal` when given, and with the along-ray footprint of one
    full-resolution pixel no larger than gsd_max_ft when given)."""
    seen = np.zeros(len(X), int)
    for k, (buf, s) in zip(kfs, zb, strict=True):
        u, v, z = k.project(X)
        ok = (z > 0.5) & (u >= 0) & (u < k.w) & (v >= 0) & (v < k.h)
        if normal is not None:
            to_cam = k.C[None, :] - X
            dist = np.linalg.norm(to_cam, axis=1)
            cosang = (to_cam @ normal) / dist
            ok &= cosang > np.cos(np.radians(max_angle))
            if gsd_max_ft is not None:
                fx_full = k.K[0] / KF_SCALE
                ok &= dist / fx_full / np.maximum(cosang, 1e-3) <= gsd_max_ft
        idx = np.where(ok)[0]
        if len(idx) == 0:
            continue
        iu = np.clip((u[idx] * s).astype(int), 0, buf.shape[1] - 1)
        iv = np.clip((v[idx] * s).astype(int), 0, buf.shape[0] - 1)
        d = buf[iv, iu]
        seen[idx] += z[idx] <= d * 1.03 + tol_ft
    return seen >= MIN_VIEWS


def cells_to_polys(mask, x0, z0, res):
    """Boolean grid [iz, ix] -> union of cell squares (shapely)."""
    polys = []
    lab, n = ndimage.label(mask)
    for i in range(1, n + 1):
        iz, ix = np.where(lab == i)
        polys.append(unary_union([box(x0 + a * res, z0 + b * res, x0 + (a + 1) * res, z0 + (b + 1) * res)
                                  for a, b in zip(ix, iz, strict=True)]))
    return unary_union(polys) if polys else Polygon()


def poly_coords(g):
    """shapely (Multi)Polygon -> list of exterior rings, rounded."""
    out = []
    for p in getattr(g, "geoms", [g]):
        if p.is_empty or p.geom_type != "Polygon":
            continue
        p = p.simplify(0.05)
        out.append([[round(x, 3), round(z, 3)] for x, z in p.exterior.coords[:-1]])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("annotations", type=Path)
    ap.add_argument("--cameras", default=None, help="override the annotation's camera set: pr13 | all")
    ap.add_argument("--suffix", default="")
    ap.add_argument("--coverage-rule", choices=["c1", "contract"], default="c1",
                    help="c1: 1 view; walls <= 75 deg, ground by 3 in pixel footprint up to 88 deg. "
                         "contract: docs/scene-and-verdict-format.md 3.1, 2 views <= 75 deg for walls and ground")
    args = ap.parse_args()
    global MIN_VIEWS, GROUND_MAX_INCIDENCE_DEG, GROUND_GSD_MAX_FT
    if args.coverage_rule == "contract":
        MIN_VIEWS, GROUND_MAX_INCIDENCE_DEG, GROUND_GSD_MAX_FT = 2, MAX_VIEW_ANGLE_DEG, None
    ann = json.loads(args.annotations.read_text())
    scene_name = ann["scene"]
    data = ETH3D / ann["dataset"]
    rng = np.random.default_rng(7)
    report = {}

    # 1. scan, ground, frame (PR #13) ---------------------------------------------------------
    if ann["dataset"] == "facade":
        P = pr13.load_scan_crop(data / "dslr_scan_eval", ann["roi_scan_xy_m"])
    else:
        P = scantools.load_scan(ann["dataset"], ann["roi_scan_xy_m"], voxel_m=None)
    P = P[np.unique(np.floor(P / 0.02).astype(np.int64), axis=0, return_index=True)[1]]  # 2 cm voxels
    g_n, g_c, g_stats = pr13.fit_ground(P, ann["ground_fit_scan_xy_m"], rng)
    frame = pr13.make_frame(g_n, g_c)
    if "origin_scan_xy_m" in ann:
        o = np.array([*ann["origin_scan_xy_m"], 0.0])
        o = o - ((o - g_c) @ g_n) * g_n
        frame = pr13.Frame(frame.A, o)
    report["ground_fit"] = g_stats
    Ps = frame.pts(P)
    plan, h = Ps[:, [0, 2]], Ps[:, 1]

    # 2. walls and joints ---------------------------------------------------------------------
    lines, stats = {}, {}
    for w in ann["walls"]:
        a, b = frame.pts(np.c_[np.array(w["rough_scan_xy_m"]), np.zeros(2)])[:, [0, 2]]
        c, d, rms, n_in = fit_wall(plan, h, a, b, rng)
        lines[w["id"]] = (c, d, a, b)
        stats[w["id"]] = {"rms_ft": rms, "inliers": n_in}
    ends = {}
    for j in ann["joints"]:
        (w1, e1), (w2, e2) = zip(j["walls"], j["ends"], strict=True)
        c1, d1 = lines[w1][:2]
        c2, d2 = lines[w2][:2]
        p = pr13.intersect_lines(c1, d1, c2, d2)
        ends[(w1, e1)] = p
        ends[(w2, e2)] = p
    for w in ann["walls"]:
        c, d, a, b = lines[w["id"]]
        for e, rough, side in (("a", a, 0), ("b", b, 1)):
            if (w["id"], e) not in ends:
                ends[(w["id"], e)] = extent_end(plan, h, c, d, rough, side)

    # outward: the side most cameras stand on
    colmap = pr13.read_colmap(data / "dslr_calibration_undistorted")
    cam_set = args.cameras or ann.get("cameras", "all")
    names = ann["pr13_keyframes"] if cam_set == "pr13" else sorted(colmap)
    kfs = [pr13.make_keyframe(colmap[n], frame, KF_SCALE, f"k{i}") for i, n in enumerate(names)]
    walls = []
    for w in ann["walls"]:
        a, b = ends[(w["id"], "a")], ends[(w["id"], "b")]
        along = (b - a) / np.linalg.norm(b - a)
        nrm = np.array([-along[1], along[0]])
        L = float(np.linalg.norm(b - a))
        near = [k for k in kfs if 0 < (k.C[[0, 2]] - a) @ along < L]
        side = np.sign(sum(np.sign((k.C[[0, 2]] - a) @ nrm) for k in (near or kfs)))
        if w.get("outward") == "flip":
            side = -side
        outward = nrm * (side if side != 0 else 1)
        lat, off = (plan - a) @ along, (plan - a) @ outward
        near_pts = (lat > 0.5) & (lat < L - 0.5) & (np.abs(off) < 0.3)
        top = float(np.percentile(h[near_pts], 99.5)) if near_pts.sum() > 50 else 8.0
        # plinth / base course proud of the fitted face (fitted at 0.3-8 ft), per 1 ft of wall: the
        # battery's back sits against the most proud part of the face at battery height
        band = (h > 0.3) & (h < 3.3) & (off > -0.3) & (off < 1.5)
        base_prof = []
        for u_ in np.arange(0.0, L, 1.0):
            m_ = band & (lat >= u_) & (lat < u_ + 1.0)
            if m_.sum() < 30:
                base_prof.append(0.0)
                continue
            hist, edges = np.histogram(off[m_], bins=np.arange(-0.3, 1.5001, 0.02))
            base_prof.append(round(max(0.0, float(edges[hist.argmax()] + 0.01)), 2))
        base_off = float(np.median(base_prof)) if base_prof else 0.0
        walls.append({
            "id": w["id"], "a": [round(float(x), 3) for x in a], "b": [round(float(x), 3) for x in b],
            "outward": [round(float(x), 4) for x in outward], "top_y": round(top, 2),
            "length_ft": round(L, 3), "base_offset_ft": round(base_off, 2), "base_profile_ft": base_prof,
            "plus_minus_ft": round(max(0.1, 3 * stats[w["id"]]["rms_ft"]), 2), "real": True,
            "fit": stats[w["id"]],
        })
    wmap = {w["id"]: w for w in walls}

    def wall_obj(wid):
        wd = wmap[wid]
        a, b = np.array(wd["a"]), np.array(wd["b"])
        al = (b - a) / np.linalg.norm(b - a)
        return a, b, al, np.array(wd["outward"])

    joints = []
    for j in ann["joints"]:
        (w1, e1), (w2, e2) = zip(j["walls"], j["ends"], strict=True)
        a1, b1, al1, n1 = wall_obj(w1)
        a2, b2, al2, n2 = wall_obj(w2)
        # direction leaving the corner along each wall
        t1 = al1 if e1 == "a" else -al1
        t2 = al2 if e2 == "a" else -al2
        # the corner is outside (convex) when wall 2 leaves the corner on wall 1's inward side
        kind = "outside" if (t2 @ n1) < -0.2 else ("inside" if (t2 @ n1) > 0.2 else "straight")
        ang = float(np.degrees(np.arccos(np.clip(t1 @ t2, -1, 1))))
        joints.append({"walls": [w1, w2], "ends": [e1, e2], "kind": kind, "angle_deg": round(ang, 1),
                       "gap_ft": 0.0})
    joined = {(j["walls"][i], j["ends"][i]) for j in joints for i in (0, 1)}
    free_ends = []
    for w in walls:
        for e in ("a", "b"):
            if (w["id"], e) not in joined:
                kind = ann.get("free_end_kinds", {}).get(f"{w['id']}.{e}", "unexplored")
                free_ends.append({"wall": w["id"], "end": e, "kind": kind})

    # 3. ground height grid -------------------------------------------------------------------
    x0, z0 = plan.min(0) - 1
    x1, z1 = plan.max(0) + 1
    nx, nz = int((x1 - x0) / GRID_FT) + 1, int((z1 - z0) / GRID_FT) + 1
    ix = ((plan[:, 0] - x0) / GRID_FT).astype(int)
    iz = ((plan[:, 1] - z0) / GRID_FT).astype(int)
    face_d = np.full(len(plan), np.inf)
    for wd in walls:
        a_, b_ = np.array(wd["a"]), np.array(wd["b"])
        al_ = (b_ - a_) / np.linalg.norm(b_ - a_)
        t_ = np.clip((plan - a_) @ al_, 0, np.linalg.norm(b_ - a_))
        face_d = np.minimum(face_d, np.linalg.norm(plan - (a_ + np.outer(t_, al_)), axis=1))
    lowm = (h < 1.5) & (face_d > 0.12)
    gh = np.full((nz, nx), np.nan)
    cnt = np.zeros((nz, nx), int)
    order = np.lexsort((h[lowm], iz[lowm], ix[lowm]))
    lix, liz, lh = ix[lowm][order], iz[lowm][order], h[lowm][order]
    key = liz * nx + lix
    uk, first, counts = np.unique(key, return_index=True, return_counts=True)
    for k_, f_, c_ in zip(uk, first, counts, strict=True):
        if c_ >= 3:
            vals = lh[f_ : f_ + c_]
            gh[k_ // nx, k_ % nx] = float(np.percentile(vals, 10))
            cnt[k_ // nx, k_ % nx] = c_
    # a cell whose 10th-percentile height is far from its neighbours' median is not ground
    med = ndimage.generic_filter(np.nan_to_num(gh, nan=np.nan), np.nanmedian, size=5, mode="nearest")
    gh[np.abs(gh - med) > 0.6] = np.nan
    # laser shadows leave sub-foot holes in flat ground; fill a hole only from at least 8 of its 24
    # neighbours within 1 ft that agree to 0.2 ft (steps and edges stay unknown)
    def _fill(v):
        c = v[12]
        if not np.isnan(c):
            return c
        nb = v[~np.isnan(v)]
        if len(nb) >= 8 and np.ptp(nb) <= 0.2:
            return float(np.median(nb))
        return np.nan
    n_nan = int(np.isnan(gh).sum())
    gh = ndimage.generic_filter(gh, _fill, size=5, mode="constant", cval=np.nan)
    report["ground_holes_filled"] = n_nan - int(np.isnan(gh).sum())

    # height of each point above the local ground
    gz = gh[np.clip(iz, 0, nz - 1), np.clip(ix, 0, nx - 1)]
    gz = np.where(np.isnan(gz), np.nanmedian(gh), gz)
    hr = h - gz

    # 4. obstacles and overheads from the scan -------------------------------------------------
    from shapely import LineString
    wall_lines = [LineString([wd["a"], wd["b"]]) for wd in walls]
    res = 0.25
    ox0, oz0 = x0, z0
    onx, onz = int((x1 - x0) / res) + 1, int((z1 - z0) / res) + 1
    oix = ((plan[:, 0] - ox0) / res).astype(int)
    oiz = ((plan[:, 1] - oz0) / res).astype(int)
    # distance of each point to the nearest wall face
    dwall = np.full(len(plan), np.inf)
    for wd in walls:
        a, b = np.array(wd["a"]), np.array(wd["b"])
        al = (b - a) / np.linalg.norm(b - a)
        L = np.linalg.norm(b - a)
        t = np.clip((plan - a) @ al, 0, L)
        dwall = np.minimum(dwall, np.linalg.norm(plan - (a + np.outer(t, al)), axis=1))
    stand = (hr > 0.3) & (hr < 6.5) & (dwall > 0.4)
    occ = np.zeros((onz, onx), int)
    np.add.at(occ, (oiz[stand], oix[stand]), 1)
    omask = occ >= 3
    lab, n = ndimage.label(omask, structure=np.ones((3, 3)))
    obstacles = []
    si = np.where(stand)[0]
    plab = lab[oiz[si], oix[si]]
    order = np.argsort(plab, kind="stable")
    bounds = np.searchsorted(plab[order], np.arange(n + 2))
    for i in range(1, n + 1):
        cz, cx = np.where(lab == i)
        if len(cz) < 2:
            continue
        sel = si[order[bounds[i] : bounds[i + 1]]]
        poly = unary_union([box(ox0 + a * res, oz0 + b * res, ox0 + (a + 1) * res, oz0 + (b + 1) * res)
                            for a, b in zip(cx, cz, strict=True)]).buffer(0.01).simplify(0.05)
        if min(poly.distance(ln) for ln in wall_lines) > 20:
            continue
        for k_, p in enumerate(getattr(poly, "geoms", [poly])):
            obstacles.append({
                "id": f"obs{len(obstacles)}", "type": "obstacle",
                "footprint": [[round(x, 3), round(z, 3)] for x, z in p.exterior.coords[:-1]],
                "y0": round(float(np.min(h[sel])), 2), "y1": round(float(np.max(h[sel])), 2),
                "plus_minus_ft": 0.1, "real": True, "note": "laser-scan standing points",
            })
    # overheads: cells with scan points 4-16 ft above ground and nothing from 0.3 to 4 ft
    lowocc = np.zeros((onz, onx), int)
    np.add.at(lowocc, (oiz[(hr > 0.3) & (hr <= 4.0)], oix[(hr > 0.3) & (hr <= 4.0)]), 1)
    ohm = (hr > 4.0) & (hr < 16.0)
    omin = np.full((onz, onx), np.inf)
    np.minimum.at(omin, (oiz[ohm], oix[ohm]), hr[ohm])
    ocnt = np.zeros((onz, onx), int)
    np.add.at(ocnt, (oiz[ohm], oix[ohm]), 1)
    omask2 = (ocnt >= 3) & (lowocc == 0) & np.isfinite(omin)
    q = np.where(omask2, np.floor(omin / 0.5), -1)
    overheads = []
    for lvl in np.unique(q[q >= 0]):
        lab, n = ndimage.label(q == lvl)
        for i in range(1, n + 1):
            cz, cx = np.where(lab == i)
            if len(cz) < 2:
                continue
            poly = unary_union([box(ox0 + a * res, oz0 + b * res, ox0 + (a + 1) * res, oz0 + (b + 1) * res)
                                for a, b in zip(cx, cz, strict=True)]).buffer(0.01).simplify(0.05)
            if min(poly.distance(ln) for ln in wall_lines) > 8:
                continue
            yb = float(np.min(omin[cz, cx])) + float(np.nanmedian(gh))
            for p in getattr(poly, "geoms", [poly]):
                overheads.append({
                    "id": f"ovh{len(overheads)}", "type": "overhead",
                    "footprint": [[round(x, 3), round(z, 3)] for x, z in p.exterior.coords[:-1]],
                    "y0": round(yb, 2), "y1": round(yb + 0.5, 2), "plus_minus_ft": 0.1, "real": True,
                })

    # 5. objects: clicked features (PR #13), scan-read features, invented objects -------------
    objects = []
    Pw = {}
    for w in walls:
        a, b, al, n_ = wall_obj(w["id"])
        Pw[w["id"]] = (a, al, n_)

    def wall_box(wid, u0, u1, y0, y1, off0=0.0, off1=0.0):
        a, al, n_ = Pw[wid]
        p = lambda u, o: [round(float(x), 3) for x in a + al * u + n_ * o]  # noqa: E731
        if off1 - off0 < 1e-6:
            return [p(u0, off0), p(u1, off0)]
        return [p(u0, off0), p(u1, off0), p(u1, off1), p(u0, off1)]

    if ann.get("pr13_features"):
        pr13_ann = json.loads((PR13 / "annotations.json").read_text())
        kf13 = {n: pr13.make_keyframe(colmap[n], frame, pr13_ann["keyframes"]["scale"], n)
                for n in pr13_ann["keyframes"]["images"]}
        pwalls = {}
        for w in walls:
            a, b = np.array(w["a"]), np.array(w["b"])
            pw = pr13.Wall(w["id"], a, b)
            lat, off = (plan - a) @ pw.along, (plan - a) @ pw.outward
            k = (lat > 1.0) & (lat < pw.length - 1.0) & (h > 4.0) & (h < 8.0) & (np.abs(off) < 1.0)
            hist, edges = np.histogram(off[k], bins=np.arange(-1.0, 1.0001, 0.02))
            pw.face_offset_ft = float(edges[hist.argmax()] + 0.01)
            pwalls[w["id"]] = pw
        for feat in pr13_ann["features"]:
            wall, mean, spread, _ = pr13.measure_feature(feat, kf13, pwalls)
            pm = max(pr13_ann["min_plus_minus_ft"],
                     spread if spread is not None else pr13_ann["single_view_plus_minus_ft"],
                     feat.get("plus_minus_floor_ft", {}).get("value", 0.0))
            u0, u1 = float(mean[0]), float(mean[1])
            objects.append({
                "id": feat["label"].replace(" ", "_"), "type": feat["type"], "wall_id": wall.id,
                "span_u": [round(u0, 3), round(u1, 3)],
                "footprint": wall_box(wall.id, u0, u1, 0, 0),
                "y0": round(max(0.0, float(mean[2])), 2), "y1": round(float(mean[3]), 2),
                "plus_minus_ft": round(float(pm), 2), "source": "photo clicks (PR #13)", "real": True,
                "attrs": feat.get("attrs", {}),
            })
    for f in ann.get("features_scan", []):
        u0, u1 = f["u"]
        objects.append({
            "id": f["id"], "type": f["type"], "wall_id": f["wall_id"], "span_u": [u0, u1],
            "footprint": wall_box(f["wall_id"], u0, u1, 0, 0), "y0": f["h"][0], "y1": f["h"][1],
            "plus_minus_ft": f.get("plus_minus_ft", 0.15), "source": "laser scan elevation", "real": True,
            "attrs": f.get("attrs", {}), "note": f.get("note", ""),
        })
    for f in ann.get("invented", []):
        u0, u1 = f["u"]
        o0, o1 = f.get("out", [0.0, 0.0])
        on_wall = f.get("mounted", o1 - o0 < 1e-6)
        objects.append({
            "id": f["id"], "type": f["type"], "wall_id": f["wall_id"] if on_wall else None,
            "span_u": [u0, u1], "footprint": wall_box(f["wall_id"], u0, u1, 0, 0, o0, o1),
            "y0": f["h"][0], "y1": f["h"][1], "plus_minus_ft": f.get("plus_minus_ft", 0.3),
            "source": "invented", "real": False, "note": f.get("note", "INVENTED"),
        })
    ground_patches = []
    for gp in ann.get("ground_types", []):
        if "polygon_scan_xy_m" in gp:
            pts = frame.pts(np.c_[np.array(gp["polygon_scan_xy_m"]), np.zeros(len(gp["polygon_scan_xy_m"]))])[:, [0, 2]]
        else:
            pts = np.array([[x0, z0], [x1, z0], [x1, z1], [x0, z1]])
        ground_patches.append({"id": gp["type"], "type": gp["type"],
                               "footprint": [[round(float(x), 3), round(float(z), 3)] for x, z in pts],
                               "plus_minus_ft": 0.3, "real": True, "note": gp.get("note", "")})

    # 6. coverage from cameras with a depth test ----------------------------------------------
    Pz = Ps[np.unique(np.floor(Ps / 0.2).astype(np.int64), axis=0, return_index=True)[1]]
    zb = camera_zbuffers(kfs, Pz)
    cov_wall = {}
    for w in walls:
        a, b, al, n_ = wall_obj(w["id"])
        L = w["length_ft"]
        us = np.arange(0.125, L, 0.25)
        n3 = np.array([n_[0], 0.0, n_[1]])
        ok = np.ones(len(us), bool)
        for yy in (0.3, 3.29):
            X = np.array([[*(a + al * u + n_ * 0.05)] for u in us])
            yg = np.array([gh[min(nz - 1, max(0, int((p[1] - z0) / GRID_FT))), min(nx - 1, max(0, int((p[0] - x0) / GRID_FT)))] for p in X])
            yg = np.where(np.isnan(yg), 0.0, yg)
            X3 = np.c_[X[:, 0], yg + yy, X[:, 1]]
            ok &= visible_any(kfs, zb, X3, n3, MAX_VIEW_ANGLE_DEG)
        iv = []
        for u, k_ in zip(us, ok, strict=True):
            if not k_:
                continue
            if iv and abs(iv[-1][1] - (u - 0.125)) < 1e-6:
                iv[-1][1] = u + 0.125
            else:
                iv.append([u - 0.125, u + 0.125])
        cov_wall[w["id"]] = [[round(p, 3), round(q_, 3)] for p, q_ in iv]
    # ground: grid cells with scanned ground that some camera sees
    gi = np.argwhere(~np.isnan(gh))
    Xg = np.c_[x0 + (gi[:, 1] + 0.5) * GRID_FT, gh[gi[:, 0], gi[:, 1]] + 0.1, z0 + (gi[:, 0] + 0.5) * GRID_FT]
    seen_g = visible_any(kfs, zb, Xg, np.array([0.0, 1.0, 0.0]), GROUND_MAX_INCIDENCE_DEG,
                         gsd_max_ft=GROUND_GSD_MAX_FT)
    gmask = np.zeros_like(gh, bool)
    gmask[gi[seen_g, 0], gi[seen_g, 1]] = True
    gmask = ndimage.binary_closing(gmask, iterations=1) & ~np.isnan(gh) | gmask
    # overhead: the probe at 7 ft above ground (or just under a scanned overhead) is visible
    # every cell within 12 ft of a wall gets a probe (NaN ground cells use the median ground height)
    from shapely import LineString as _LS
    from shapely import points as _pts
    walls_u = unary_union([_LS([wd["a"], wd["b"]]) for wd in walls])
    ci = np.argwhere(np.ones_like(gh, bool))
    cx_, cz_ = x0 + (ci[:, 1] + 0.5) * GRID_FT, z0 + (ci[:, 0] + 0.5) * GRID_FT
    near_w = np.array([walls_u.distance(p_) <= 12.0 for p_ in _pts(np.c_[cx_, cz_])])
    ci = ci[near_w]
    gfill = np.where(np.isnan(gh), np.nanmedian(gh), gh)
    Xo = np.c_[x0 + (ci[:, 1] + 0.5) * GRID_FT, gfill[ci[:, 0], ci[:, 1]] + 7.0, z0 + (ci[:, 0] + 0.5) * GRID_FT]
    seen_o = visible_any(kfs, zb, Xo)
    omaskc = np.zeros_like(gh, bool)
    omaskc[ci[seen_o, 0], ci[seen_o, 1]] = True
    ground_obs = cells_to_polys(gmask, x0, z0, GRID_FT)
    over_obs = cells_to_polys(omaskc, x0, z0, GRID_FT)
    # overhead cells where the scan saw an overhead also count as observed
    ovh_union = unary_union([Polygon(o["footprint"]) for o in overheads]) if overheads else Polygon()
    over_obs = unary_union([over_obs, ovh_union.intersection(ground_obs.buffer(0.5))])

    # 7. interior (building mass) -------------------------------------------------------------
    interior = []
    for poly in ann.get("interior_scan_xy_m", []):
        pts = frame.pts(np.c_[np.array(poly), np.zeros(len(poly))])[:, [0, 2]]
        interior.append([[round(float(x), 3), round(float(z), 3)] for x, z in pts])

    # 8. meters (invented) -> one scene per meter placement ----------------------------------
    OUT.mkdir(parents=True, exist_ok=True)
    gh_json = [[None if np.isnan(v) else round(float(v), 2) for v in row] for row in gh]
    written = []
    for m in ann["meters"]:
        a, b, al, n_ = wall_obj(m["wall_id"])
        u = m["u_ft"] if "u_ft" in m else float(np.linalg.norm(b - a)) * m["u_frac"]
        p = a + al * u
        yg = gh[min(nz - 1, max(0, int((p[1] - z0) / GRID_FT))), min(nx - 1, max(0, int((p[0] - x0) / GRID_FT)))]
        yg = 0.0 if np.isnan(yg) else float(yg)
        scene = {
            "format": "c1-scene3d/0",
            "id": f"{scene_name}{args.suffix}-{m['id']}",
            "units": "ft",
            "frame": "PR #13 scene frame: y = fitted ground normal (up), x = scan x made horizontal, z = x cross y",
            "provenance": {
                "dataset": f"ETH3D {ann['dataset']} (research-only licence, CC BY-NC-SA)",
                "real": "walls, joints, ground heights, overheads, obstacles, coverage and scan-read / photo-clicked openings",
                "invented": "the electric meter" + (" and " + ", ".join(f["id"] for f in ann.get("invented", [])) if ann.get("invented") else ""),
                "cameras": cam_set, "n_cameras": len(kfs), "coverage_rule": args.coverage_rule,
            },
            "meter": {"pos": [round(float(p[0]), 3), round(yg + m.get("height_ft", 5.0), 3), round(float(p[1]), 3)],
                      "wall_id": m["wall_id"], "plus_minus_ft": 0.3, "real": False, "note": m.get("note", "INVENTED")},
            "walls": walls, "joints": joints, "free_ends": free_ends,
            "objects": objects, "obstacles": obstacles, "overheads": overheads,
            "ground_patches": ground_patches,
            "ground": {"grid": {"x0": round(float(x0), 3), "z0": round(float(z0), 3), "res": GRID_FT, "h": gh_json}},
            "interior": interior,
            "coverage": {"wall": cov_wall, "ground": mapping(ground_obs), "overhead": mapping(over_obs)},
            "meta": {"ground_fit": g_stats, "wall_fit": stats,
                     "frame_A": frame.A.round(9).tolist(), "frame_origin_scan_m": frame.origin.round(6).tolist()},
        }
        path = OUT / f"{scene['id']}.json"
        path.write_text(json.dumps(scene))
        written.append(str(path))
    summary = {
        "walls": [{k: w[k] for k in ("id", "length_ft", "top_y", "base_offset_ft", "plus_minus_ft")} for w in walls],
        "joints": joints, "free_ends": free_ends,
        "n_obstacles": len(obstacles), "n_overheads": len(overheads),
        "ground_holes_filled": report.get("ground_holes_filled"),
        "coverage_wall": cov_wall,
        "ground_observed_ft2": round(ground_obs.area, 1), "overhead_observed_ft2": round(over_obs.area, 1),
        "objects": [(o["id"], o["type"], o.get("wall_id"), o["span_u"], o["y0"], o["y1"], o["plus_minus_ft"]) for o in objects],
        "written": written,
    }
    print(json.dumps(summary, indent=1, default=float))


if __name__ == "__main__":
    main()
