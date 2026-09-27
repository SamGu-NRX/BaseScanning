"""Geometry scorer: a reconstruction vs a site's ground-truth scene.

Two entry points (contract: docs/scene-and-verdict-format.md section 6.1):

- `score_scene(gt_scene, recon_scene)`: the recon is already a scene v0 (meter-anchored frame).
- `score_cloud(gt_scene, cloud, cameras, metric)`: a bare point cloud or mesh in any frame plus
  the cameras that made it. The harness aligns it (similarity from camera centres to the GT
  cameras; rigid only when `metric`, so real scale error stays in the numbers), builds a scene
  with the reference extractor using perfect GT taps/clicks, then calls `score_scene`.

Output: key-distance errors (inches), completeness of the criteria regions, scale error, anchor
error, coverage honesty and geometry in unobserved space; JSON + Markdown.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

try:
    import pycolmap
except ImportError:
    pycolmap = None

from server.eval.chain import Chain, ground_y, object_center
from server.eval.coverage import (GROUND_RULES, MIN_VIEWS, CoverageQuery, bands_from_grid, build_grid, coverage_rule, grade,
                                  ground_sensitivity, limit_for, query_region, set_wall_observed_fraction)
from server.eval.extract import ExtractParams, extract_scene, profile_entries
from server.eval.frame import align_by_rotations, apply, collinearity, mat4_from_list, pose_cv_to_arkit, scale_of, transform_pose, umeyama
from server.eval.keydist import key_distances, stations_from
from server.eval.match import match, renamed
from server.eval.sceneio import load_coverage, load_json, load_points, read_ply, save_json, sha256_file, write_ply
from server.eval.taps import meter_taps, opening_clicks
from server.eval.visibility import Camera, VisibilityOracle

IN_PER_M = 1 / 0.0254
REACH_M = 7.92  # 26 ft, the longest reach in the three profiles
HEADROOM_M = 2.13
GROUND_OUT_M = 4.27  # 14 ft
OVERHEAD_OUT_M = 0.91
NEAR_M = 10.0
COLLINEAR_RATIO = 0.2  # auto alignment uses camera rotations when the centres are this close to a line
LENGTH_CATS = ("corner.s", "wall.length", "opening.edge_s", "opening.gap", "equipment.meter_dist")


# --- helpers ---------------------------------------------------------------------------------


def icp_rigid(src: np.ndarray, dst: np.ndarray, iters: int = 40, dists=(1.0, 0.5, 0.25, 0.15)) -> np.ndarray:
    tree = cKDTree(dst)
    T = np.eye(4)
    cur = src.copy()
    for k in range(iters):
        dmax = dists[min(k * len(dists) // iters, len(dists) - 1)]
        d, j = tree.query(cur, distance_upper_bound=dmax)
        ok = np.isfinite(d)
        if ok.sum() < 30:
            break
        dT = umeyama(cur[ok], dst[j[ok]], with_scale=False)
        cur = apply(dT, cur)
        T = dT @ T
    return T


def err_stats(err_in: np.ndarray, within_own: np.ndarray | None = None) -> dict:
    a = np.abs(np.asarray(err_in, float))
    if len(a) == 0:
        return {"n": 0}
    out = {"n": int(len(a)), "median_in": round(float(np.median(a)), 2), "p90_in": round(float(np.percentile(a, 90)), 2),
           "max_in": round(float(a.max()), 2), "share_le_2in": round(float((a <= 2).mean()), 3),
           "share_le_4in": round(float((a <= 4).mean()), 3), "share_le_6in": round(float((a <= 6).mean()), 3)}
    if within_own is not None:
        out["calibration"] = round(float(np.mean(within_own)), 3)
    return out


def _sub(X: np.ndarray, n: int, seed: int = 0) -> np.ndarray:
    if len(X) <= n:
        return X
    return X[np.random.default_rng(seed).choice(len(X), n, replace=False)]


def criteria_regions(gt: dict, laser: np.ndarray) -> dict[str, dict]:
    """GT surface samples and coverage probes of each criteria region."""
    ch = Chain(gt)
    meter = next(o for o in gt["objects"] if o["id"] == gt["frame"]["anchor"]["element_id"])
    mwall = meter.get("wall_id") or ch.nearest_wall(object_center(meter, ch)[[0, 2]]).id
    idx, s, out = ch.locate(laser[:, [0, 2]])
    h = laser[:, 1] - ground_y(gt, laser[:, [0, 2]])
    interior = (s > ch.s_min + 1e-6) & (s < ch.s_max - 1e-6) & (np.abs(s) <= REACH_M)
    wid = np.array([w.id for w in ch.walls])[idx]
    outw = np.array([[w.outward[0], 0.0, w.outward[1]] for w in ch.walls])[idx]
    face = interior & (np.abs(out) < 0.15) & (h >= 0) & (h <= HEADROOM_M)
    ground = interior & (np.abs(h) < 0.1) & (out > 0.1) & (out < GROUND_OUT_M)
    regions = {
        "meter_wall": {"mask": face & (wid == mwall), "probe_off": outw * 0.1, "normal": outw},
        "wall_face": {"mask": face, "probe_off": outw * 0.1, "normal": outw},
        "ground_strip": {"mask": ground, "probe_off": np.tile([0.0, 0.05, 0.0], (len(laser), 1)),
                         "normal": np.tile([0.0, 1.0, 0.0], (len(laser), 1))},
    }
    vol = []
    for w in ch.walls:
        for sv in np.arange(max(w.s0, -REACH_M) + 0.125, min(w.s1, REACH_M), 0.25):
            q = w.plan_at(sv)
            for o in (0.2, 0.5, OVERHEAD_OUT_M):
                for hv in np.arange(0.3, HEADROOM_M + 1e-9, 0.25):
                    p = q + o * w.outward
                    vol.append([p[0], ground_y(gt, p[None])[0] + hv, p[1]])
    vol_mask = interior & (out > 0.15) & (out < OVERHEAD_OUT_M) & (h > 0.15) & (h <= HEADROOM_M)
    regions["overhead"] = {"mask": vol_mask, "volume_probes": np.array(vol)}
    return regions


# --- scene vs scene --------------------------------------------------------------------------


def score_scene(gt_path: Path, rc_path: Path, out_dir: Path | None = None, visibility: bool = True,
                align_info: dict | None = None) -> dict:
    t0 = time.time()
    gt_path, rc_path = Path(gt_path), Path(rc_path)
    gt, rc0 = load_json(gt_path), load_json(rc_path)
    m = match(gt, rc0)
    rc = renamed(rc0, m)
    st = stations_from(gt)
    kg, kr = key_distances(gt, st), key_distances(rc, st)
    skip = set(rc["provenance"].get("scale_reference_ids", []))

    rows = []
    for k, g in kg.items():
        r = kr.get(k)
        if k in skip:
            status = "scale_reference"
        elif r is None:
            status = "missing"
        elif g["extent_limited"] or r["extent_limited"]:
            status = "extent_limited"
        else:
            status = "scored"
        row = {"id": k, "category": g["category"], "feeds": g["feeds"], "unit": g["unit"], "gt": round(g["value"], 4),
               "status": status}
        if r is not None:
            row["recon"] = round(r["value"], 4)
            row["recon_pm"] = round(r["pm"], 4)
            e = r["value"] - g["value"]
            row["error"] = round(e * (IN_PER_M if g["unit"] == "m" else 1.0), 3)
            row["within_own_bound"] = bool(abs(e) <= r["pm"])
        rows.append(row)
    for k, r in kr.items():
        if k not in kg and not k.startswith(("facing.", "overhead.")):
            rows.append({"id": k, "category": r["category"], "feeds": r["feeds"], "unit": r["unit"], "recon": round(r["value"], 4),
                         "status": "phantom"})

    sc = [r for r in rows if r["status"] == "scored" and r["unit"] == "m"]
    sd = [r for r in rows if r["status"] == "scored" and r["unit"] == "deg"]
    by_cat = {}
    for c in sorted({r["category"] for r in sc}):
        rr = [r for r in sc if r["category"] == c]
        by_cat[c] = err_stats([r["error"] for r in rr], [r["within_own_bound"] for r in rr])
    status_counts = {s: sum(r["status"] == s for r in rows) for s in ("scored", "missing", "extent_limited", "phantom", "scale_reference")}

    # scale
    ratios = [kr[r["id"]]["value"] / kg[r["id"]]["value"] for r in sc
              if r["category"] in LENGTH_CATS and abs(kg[r["id"]]["value"]) > 3.0 and kg[r["id"]]["value"] * kr[r["id"]]["value"] > 0]
    g_ch, r_ch = Chain(gt), Chain(rc)
    lm_g, lm_r = [], []
    for o in rc["objects"]:
        go = next((x for x in gt["objects"] if x["id"] == o["id"]), None)
        if go is not None:
            lm_g.append(object_center(go, g_ch))
            lm_r.append(object_center(o, r_ch))
    for c in rc["chain"].get("corners", []):
        gc = next((x for x in gt["chain"].get("corners", []) if x["id"] == c["id"]), None)
        if gc is not None:
            lm_g.append([gc["point_m"][0], 0.0, gc["point_m"][1]])
            lm_r.append([c["point_m"][0], 0.0, c["point_m"][1]])
    scale = {"kd_ratio_median": round(float(np.median(ratios)), 5) if ratios else None, "kd_ratio_n": len(ratios)}
    if len(lm_g) >= 3:
        U = umeyama(np.array(lm_r), np.array(lm_g))
        scale["landmark_umeyama_scale"] = round(1 / scale_of(U), 5)
        scale["landmarks"] = len(lm_g)
    if scale["kd_ratio_median"] is not None:
        scale["scale_error_pct"] = round(100 * (scale["kd_ratio_median"] - 1), 3)
    if align_info:
        scale["alignment"] = align_info

    result = {"format": "base-geomscore/v0", "gt": str(gt_path), "recon": str(rc_path),
              "gt_scene_id": gt["scene_id"], "recon_scene_id": rc0["scene_id"], "producer": rc0["provenance"]["producer"]["path"],
              "match": m, "key_distances": {"overall": err_stats([r["error"] for r in sc], [r["within_own_bound"] for r in sc]),
                                            "by_category": by_cat, "angles_deg": err_stats([r["error"] for r in sd]) if sd else {"n": 0},
                                            "status_counts": status_counts, "rows": rows},
              "scale": scale}
    if sd:
        result["key_distances"]["angles_deg"] = {"n": len(sd), "max_abs_deg": round(max(abs(r["error"]) for r in sd), 3)}

    # point-based parts
    laser, _, _ = load_points(gt, gt_path.parent, asset_id="laser") if any(a["id"] == "laser" for a in gt.get("assets", [])) \
        else load_points(gt, gt_path.parent)
    rpts, rgen, _ = load_points(rc, rc_path.parent)
    if rpts is None:
        rpts, rgen, _ = load_points(rc, rc_path.parent, kind="mesh")
    if laser is not None and rpts is not None:
        result.update(_point_scores(gt, gt_path, rc, rc_path, laser, rpts, rgen, visibility))
    kc = result.get("key_distance_coverage", {})
    for row in rows:
        if row["id"] == "meter.height":
            oid = gt["frame"]["anchor"]["element_id"]
        else:
            oid = row["id"].split("[")[1].split(":")[0].rstrip("]") if "[" in row["id"] else None
        if oid in kc:
            row["coverage_truth"] = kc[oid]["truth"]["status"]
            if "claimed" in kc[oid]:
                row["coverage_claimed"] = kc[oid]["claimed"]["status"]
    obs_rows = [r for r in sc if r.get("coverage_truth") == "observed"]
    if kc:
        result["key_distances"]["on_truly_observed_elements"] = err_stats([r["error"] for r in obs_rows], [r["within_own_bound"] for r in obs_rows])
        result["key_distances"]["on_grazing_or_unobserved_elements"] = err_stats(
            [r["error"] for r in sc if r.get("coverage_truth") not in (None, "observed")])
        result["key_distances"]["false_observed_claims"] = sorted(k for k, v in kc.items() if v["false_observed_claim"])
    result["runtime_s"] = round(time.time() - t0, 1)
    if out_dir:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "geomscore.json").write_text(json.dumps(result, indent=1, default=float) + "\n")
        (out_dir / "geomscore.md").write_text(markdown(result))
    return result


def _point_scores(gt, gt_path, rc, rc_path, laser, rpts, rgen, visibility: bool) -> dict:
    out: dict = {}
    near_r = np.linalg.norm(rpts[:, [0, 2]], axis=1) < NEAR_M + 2
    near_l = np.linalg.norm(laser[:, [0, 2]], axis=1) < NEAR_M + 2
    T = icp_rigid(_sub(rpts[near_r], 150_000), laser[near_l]) if near_r.sum() > 50 else np.eye(4)
    R = T[:3, :3]
    out["anchor"] = {"icp_translation_m": [round(float(v), 4) for v in T[:3, 3]],
                     "icp_translation_norm_m": round(float(np.linalg.norm(T[:3, 3])), 4),
                     "icp_yaw_deg": round(float(np.degrees(np.arctan2(R[0, 2], R[0, 0]))), 3),
                     "icp_tilt_deg": round(float(np.degrees(np.arccos(np.clip(R[1, 1], -1, 1)))), 3),
                     "meter_origin_offset_m": round(float(np.linalg.norm(apply(T, np.zeros((1, 3)))[0])), 4),
                     "note": "Rigid ICP of recon points onto the laser (within 12 m of the meter). The translation is how far the recon's meter frame sits from the truth."}
    regions = criteria_regions(gt, laser)
    tree_decl = cKDTree(rpts)
    tree_icp = cKDTree(apply(T, rpts))
    grid = load_coverage(rc, rc_path.parent)
    oracle = None
    if visibility:
        gkf = {k["id"]: k for k in gt.get("keyframes", [])}
        ids = [k["id"] for k in rc.get("keyframes", []) if k["id"] in gkf] or list(gkf)
        cams = [Camera(i, mat4_from_list(gkf[i]["pose"]), np.array(gkf[i]["intrinsics"]), gkf[i]["w"], gkf[i]["h"]) for i in ids]
        blockers = [laser]
        if any(a["id"] == "occluders" for a in gt.get("assets", [])):
            occ, _, _ = load_points(gt, gt_path.parent, asset_id="occluders")
            blockers.append(occ)
        truth_q = CoverageQuery(cams, np.vstack(blockers))
        oracle = truth_q.oracle
        out["visibility"] = {"cameras": len(cams), "rule": coverage_rule(),
                             "truth": "GT cameras used by the recon, laser z-buffer + occluders"}
        rc_q = CoverageQuery.from_scene(rc, rc_path.parent) if rc.get("keyframes") else None
        out["key_distance_coverage"] = _kd_coverage(gt, rc, truth_q, rc_q)
    comp = {}
    for name, rg in regions.items():
        mk = np.flatnonzero(rg["mask"])
        entry: dict = {"gt_samples": int(len(mk))}
        if len(mk):
            sel = _sub(mk, 60_000)
            S = laser[sel]
            for tag, tree in (("declared", tree_decl), ("after_icp", tree_icp)):
                d, _ = tree.query(S, distance_upper_bound=0.2)
                entry[tag] = {f"within_{int(c * 100)}cm": round(float((d <= c).mean()), 4) for c in (0.05, 0.10, 0.20)}
        else:
            entry["note"] = "no GT surface in this region (open space); coverage only"
        if "volume_probes" in rg:
            probes, normals = rg["volume_probes"], None
        elif len(mk):
            probes, normals = S + rg["probe_off"][sel], rg["normal"][sel]
        else:
            continue
        cov: dict = {"probes": int(len(probes))}
        claimed = grid.observed_at(probes) if grid is not None else np.zeros(len(probes), bool)
        cov["claimed_observed"] = round(float(claimed.mean()), 4) if grid is not None else None
        if oracle is not None:
            truly = oracle.seen_by(probes, normals).any(1)
            cov["truly_seen"] = round(float(truly.mean()), 4)
            cov["false_observed"] = round(float((claimed & ~truly).mean()), 4)
            cov["missed_observed"] = round(float((~claimed & truly).mean()), 4)
            surf = (S, normals) if normals is not None else (probes, None)
            kind = "ground" if name == "ground_strip" else ("wall" if normals is not None else "free")
            t_obs = truth_q.incidences(*surf)
            r_obs = rc_q.incidences(*surf) if rc_q is not None else None
            limits = [(None, limit_for(kind), None)] + ([(rid, r["max_incidence_deg"], r.get("max_footprint_m")) for rid, r in GROUND_RULES.items()]
                                                  if kind == "ground" else [])
            for tag, lim, mfp in limits:
                t_pp = truth_q.summarize(t_obs[0], t_obs[1], lim, t_obs[2], mfp)
                t_ok = t_pp["views_good"] >= MIN_VIEWS
                c = {"max_incidence_deg": lim, "truth_by_rule": grade(t_pp, {"region": name}, surface=kind, limit=lim, max_fp=mfp)["status"],
                     "truly_observed_by_rule": round(float(t_ok.mean()), 4),
                     "truly_grazing_only": round(float(((t_pp["views_any"] >= 1) & ~t_ok).mean()), 4)}
                if tag:
                    c["rule_id"] = tag
                    if mfp is not None:
                        c["max_footprint_m"] = mfp
                if r_obs is not None:
                    r_ok = rc_q.summarize(r_obs[0], r_obs[1], lim, r_obs[2], mfp)["views_good"] >= MIN_VIEWS
                    c["claimed_by_rule"] = round(float(r_ok.mean()), 4)
                    c["false_observed_by_rule"] = round(float((r_ok & ~t_ok).mean()), 4)
                if tag is None:
                    cov.update(c)
                else:
                    cov.setdefault("sensitivity", []).append(c)
        entry["coverage"] = cov
        comp[name] = entry
    out["completeness"] = comp
    rn = rpts[np.linalg.norm(rpts[:, [0, 2]], axis=1) < NEAR_M]
    un: dict = {"recon_points_near": int(len(rn)), "near_radius_m": NEAR_M}
    if oracle is not None and len(rn):
        sub = _sub(rn, 200_000)
        un["share_unseen_by_any_input_camera"] = round(float((~oracle.seen_by(sub, None).any(1)).mean()), 4)
    if grid is not None and len(rn):
        un["share_in_own_unknown_voxels"] = round(float((grid.state_at(rn) == 0).mean()), 4)
    un["share_flagged_generated"] = round(float(rgen[np.linalg.norm(rpts[:, [0, 2]], axis=1) < NEAR_M].mean()), 4) if rgen is not None else 0.0
    un["generated_regions"] = len(rc.get("generated_regions", []))
    out["unobserved_geometry"] = un
    return out


def _kd_coverage(gt: dict, rc: dict, truth_q: CoverageQuery, rc_q: CoverageQuery | None) -> dict:
    """Coverage evidence of the element behind each opening / meter key distance: what the recon
    would claim (its own cameras and points) and what the truth says. A key distance measured on a
    region the truth grades grazing or unobserved must lead a criteria path to unsure."""
    out = {}
    g_ch = Chain(gt)
    anchor = gt["frame"]["anchor"]["element_id"]
    rc_ids = {o["id"]: o for o in rc["objects"]}
    rc_walls = {w["id"] for w in rc["walls"]}
    for o in gt["objects"]:
        g = o["geometry"]
        if o["id"] == anchor:
            c = object_center(o, g_ch)
            w = o.get("wall_id") or g_ch.nearest_wall(c[[0, 2]]).id
            reg = {"wall_id": w, "s_m": [-0.3, 0.3], "height_m": [max(0.0, c[1] - 0.3), c[1] + 0.3]}
        elif g["kind"] == "wall_rect":
            reg = {"wall_id": g["wall_id"], "s_m": list(g["s_m"]), "height_m": [g["bottom_m"], min(g["top_m"], HEADROOM_M + 1.5)]}
        else:
            continue
        e = {"region": reg, "truth": query_region(gt, None, reg, truth_q)}
        ro = rc_ids.get(o["id"])
        if rc_q is not None and ro is not None:
            rg = ro["geometry"]
            rreg = dict(reg)
            if rg["kind"] == "wall_rect" and rg["wall_id"] in rc_walls:
                rreg = {"wall_id": rg["wall_id"], "s_m": list(rg["s_m"]), "height_m": [rg["bottom_m"], min(rg["top_m"], HEADROOM_M + 1.5)]}
            if rreg["wall_id"] in rc_walls:
                e["claimed"] = query_region(rc, None, rreg, rc_q)
        e["false_observed_claim"] = bool(e.get("claimed", {}).get("status") == "observed" and e["truth"]["status"] != "observed")
        out[o["id"]] = e
    return out


def markdown(r: dict) -> str:
    kd = r["key_distances"]
    L = [f"# Geometry score: {r['recon_scene_id']} vs {r['gt_scene_id']}", "",
         f"Producer `{r['producer']}`. Status counts: {kd['status_counts']}.", ""]
    o = kd["overall"]
    if o.get("n"):
        L += [f"**Key distances ({o['n']} scored):** median {o['median_in']} in, p90 {o['p90_in']} in, max {o['max_in']} in; "
              f"≤2 in {o['share_le_2in']:.0%}, ≤4 in {o['share_le_4in']:.0%}, ≤6 in {o['share_le_6in']:.0%}; "
              f"within the recon's own bound {o.get('calibration', 0):.0%}.", ""]
    s = r["scale"]
    L += [f"**Scale:** KD ratio median {s.get('kd_ratio_median')} (n={s.get('kd_ratio_n')}, error {s.get('scale_error_pct')} %), "
          f"landmark Umeyama {s.get('landmark_umeyama_scale')}." + (f" Alignment: {s['alignment']}." if s.get("alignment") else ""), ""]
    if "anchor" in r:
        a = r["anchor"]
        L += [f"**Anchor:** ICP translation {a['icp_translation_norm_m']} m {a['icp_translation_m']}, yaw {a['icp_yaw_deg']}°, tilt {a['icp_tilt_deg']}°.", ""]
    if kd.get("false_observed_claims") is not None:
        t = kd.get("on_truly_observed_elements", {})
        L += [f"**On truly observed elements:** {t.get('n', 0)} KDs, median {t.get('median_in')} in, p90 {t.get('p90_in')} in. "
              f"Elements the recon would claim observed but the truth grades grazing/unobserved: {kd['false_observed_claims'] or 'none'}.", ""]
    L += ["| Key distance | Status | GT | Recon | Error | ± own | within | coverage claimed / truth |", "|---|---|---|---|---|---|---|---|"]
    for row in kd["rows"]:
        u = "in" if row["unit"] == "m" else "deg"
        L.append(f"| `{row['id']}` | {row['status']} | {row.get('gt', '')} | {row.get('recon', '')} | "
                 f"{row.get('error', '')} {u if 'error' in row else ''} | {row.get('recon_pm', '')} | {row.get('within_own_bound', '')} | "
                 f"{row.get('coverage_claimed', '–')} / {row.get('coverage_truth', '–')} |")
    if "completeness" in r:
        L += ["", "| Region | GT samples | ≤5/10/20 cm declared | ≤5/10/20 cm after ICP | grid claims observed | truly seen | false observed (grid) "
              "| truth by rule (status, share, grazing-only) | recon claims by rule | false observed by rule |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for name, e in r["completeness"].items():
            fmt = lambda d: "/".join(f"{v:.0%}" for v in d.values()) if d else "–"  # noqa: E731
            c = e["coverage"]
            L.append(f"| {name} | {e['gt_samples']} | {fmt(e.get('declared'))} | {fmt(e.get('after_icp'))} | "
                     f"{c.get('claimed_observed')} | {c.get('truly_seen')} | {c.get('false_observed')} | "
                     f"{c.get('truth_by_rule')}, {c.get('truly_observed_by_rule')}, {c.get('truly_grazing_only')} | "
                     f"{c.get('claimed_by_rule')} | {c.get('false_observed_by_rule')} |")
        u = r["unobserved_geometry"]
        L += ["", f"**Unobserved geometry** (recon points within {u['near_radius_m']} m): unseen by any input camera "
              f"{u.get('share_unseen_by_any_input_camera')}, in own unknown voxels {u.get('share_in_own_unknown_voxels')}, "
              f"flagged generated {u.get('share_flagged_generated')}."]
    return "\n".join(L) + "\n"


# --- bare cloud ------------------------------------------------------------------------------


def load_cameras(path: Path) -> dict[str, dict]:
    """image stem -> {pose (ARKit c2w 4x4 in the cloud's frame), K, w, h}. Accepts a COLMAP model
    directory (text or binary) or a JSON list of {image, pose (16, column-major, ARKit axes) |
    R_cw + t_cw (OpenCV world-to-camera), intrinsics [fx, fy, cx, cy], w, h}."""
    path = Path(path)
    out = {}
    if path.is_dir():
        if pycolmap is None:
            raise ImportError("reading a COLMAP model needs pycolmap (pip install pycolmap)")
        rec = pycolmap.Reconstruction(str(path))
        for im in rec.images.values():
            cfw = im.cam_from_world() if callable(im.cam_from_world) else im.cam_from_world
            cam = rec.cameras[im.camera_id]
            out[Path(im.name).stem] = {"pose": pose_cv_to_arkit(cfw.rotation.matrix(), np.asarray(cfw.translation)),
                                       "K": np.asarray(cam.params[:4], float) if cam.model.name in ("PINHOLE", "OPENCV", "FULL_OPENCV")
                                       else np.array([cam.params[0], cam.params[0], cam.params[1], cam.params[2]]),
                                       "w": int(cam.width), "h": int(cam.height)}
        return out
    for c in json.loads(path.read_text()):
        pose = mat4_from_list(c["pose"]) if "pose" in c else pose_cv_to_arkit(np.asarray(c["R_cw"], float), np.asarray(c["t_cw"], float))
        out[Path(c["image"]).stem] = {"pose": pose, "K": np.asarray(c["intrinsics"], float), "w": int(c["w"]), "h": int(c["h"])}
    return out


def load_cloud(path: Path) -> np.ndarray:
    d = read_ply(Path(path))
    return np.c_[d["x"], d["y"], d["z"]].astype(float)


def score_cloud(gt_path: Path, cloud_path: Path, cameras_path: Path | None, out_dir: Path, metric: bool,
                producer: str = "cloud", transform: np.ndarray | None = None, visibility: bool = True, align_mode: str = "auto",
                params: ExtractParams | None = None, max_points: int = 400_000) -> dict:
    """Align a bare cloud to the GT frame, build a scene with the reference extractor, score it."""
    t0 = time.time()
    gt_path, out_dir = Path(gt_path), Path(out_dir)
    gt = load_json(gt_path)
    X = load_cloud(cloud_path)
    gkf = {k["id"]: k for k in gt["keyframes"]}
    cams_src = load_cameras(cameras_path) if cameras_path else {}
    names = sorted(n for n in cams_src if n in gkf)
    align: dict = {"metric_declared": metric}
    if transform is not None:
        T = np.asarray(transform, float)
        align["method"] = "given transform"
    else:
        if len(names) < 3:
            raise ValueError(f"need >= 3 cameras whose image names match the GT keyframes (got {len(names)})")
        c_src = np.array([cams_src[n]["pose"][:3, 3] for n in names])
        c_gt = np.array([mat4_from_list(gkf[n]["pose"])[:3, 3] for n in names])
        R_src = np.array([cams_src[n]["pose"][:3, :3] for n in names])
        R_gt = np.array([mat4_from_list(gkf[n]["pose"])[:3, :3] for n in names])
        col = collinearity(c_src)
        mode = align_mode if align_mode != "auto" else ("rotations" if col < COLLINEAR_RATIO else "centres")
        align.update({"mode": mode, "requested": align_mode, "centre_collinearity": round(col, 4)})
        if mode == "rotations":
            S = align_by_rotations(R_src, c_src, R_gt, c_gt, with_scale=True)
            T = S if not metric else align_by_rotations(R_src, c_src, R_gt, c_gt, with_scale=False)
            how = "camera rotations (chordal mean) + centres"
        else:
            S = umeyama(c_src, c_gt, with_scale=True)
            T = S if not metric else umeyama(c_src, c_gt, with_scale=False)
            how = "camera centres (Umeyama)"
        align["similarity_scale"] = round(scale_of(S), 6)
        if metric:
            align["method"] = f"rigid (metric cloud): {how} -> GT cameras"
            align["cloud_scale_error_pct"] = round(100 * (1 / scale_of(S) - 1), 3)
        else:
            align["method"] = f"similarity (unscaled cloud): {how} -> GT cameras; scale error not meaningful"
        R_al = T[:3, :3] / scale_of(T)
        align["rotation_residual_deg_median"] = round(float(np.median([np.degrees(np.arccos(np.clip((np.trace(g.T @ R_al @ r) - 1) / 2, -1, 1)))
                                                                        for g, r in zip(R_gt, R_src)])), 3)
        align["camera_rms_m"] = round(float(np.sqrt(np.mean(np.sum((apply(T, c_src) - c_gt) ** 2, 1)))), 4)
        align["cameras"] = len(names)
    Xw = apply(T, X)
    keep = np.linalg.norm(Xw[:, [0, 2]], axis=1) < 40
    Xw = _sub(Xw[keep], max_points, seed=1)

    gt_cams = [Camera(n, mat4_from_list(gkf[n]["pose"]), np.array(gkf[n]["intrinsics"]), gkf[n]["w"], gkf[n]["h"]) for n in names]
    cams = [Camera(n, transform_pose(T, cams_src[n]["pose"]), cams_src[n]["K"], cams_src[n]["w"], cams_src[n]["h"]) for n in names]
    w_out = {i: c.w for i, c in enumerate(cams)}
    taps = meter_taps(gt, gt_cams, w_out)
    feats = opening_clicks(gt, gt_cams, w_out)
    for f in feats:
        f["source_model"] = "perfect clicks: GT opening edges projected through GT cameras"
    vis = VisibilityOracle(cams, Xw)
    seen = vis.seen_by(Xw, None)
    tracks = [np.flatnonzero(r) for r in seen]
    p = params or ExtractParams(scale_rel_pm=0.0 if not metric else 0.03)
    prov = {"data": gt["provenance"]["data"], "site": gt["provenance"]["site"],
            "producer": {"path": producer, "params": {"alignment": align, "extract": asdict(p), "cloud": str(cloud_path)}},
            "inputs": {"images_used": len(names)}, "scale_source": "native_metric" if metric else "ground_truth",
            "gravity_source": "ground_truth", "notes": ["Scene built by server.eval.geomscore.score_cloud (reference extractor, perfect GT taps and clicks)."]}
    scene, st = extract_scene(Xw, tracks, cams, taps, feats, p, prov, f"{gt['provenance']['site']}/{producer}", "producer_cloud", T)
    scene["overheads"], scene["facing"] = profile_entries(st["points_c"], scene, pm_m=0.05, min_count=p.profile_min_count)
    xs = [q[0] for w in scene["walls"] for q in w["baseline_m"]]
    zs = [q[1] for w in scene["walls"] for q in w["baseline_m"]]
    lo, hi = np.array([min(xs) - 4, -0.5, min(zs) - 2]), np.array([max(xs) + 4, 6.0, max(zs) + 16])
    by_cam: dict[int, list[int]] = {}
    for pi, tr in enumerate(tracks):
        for ci in tr:
            by_cam.setdefault(int(ci), []).append(pi)
    grid = build_grid(((st["cams_c"][ci].pose[:3, 3], st["points_c"][np.array(v)], None) for ci, v in sorted(by_cam.items())), lo, hi, p.voxel_m)
    out_dir.mkdir(parents=True, exist_ok=True)
    grid.save(out_dir / "coverage.npz")
    write_ply(out_dir / "points.ply", st["points_c"].astype(np.float32))
    scene["coverage"] = {"method": "rays from each camera to the cloud points it sees (cloud z-buffer)", "grid": grid.meta("coverage"),
                         "rule": coverage_rule()}
    query = CoverageQuery(st["cams_c"], st["points_c"])
    scene["coverage"]["bands"] = bands_from_grid(grid, scene, query=query)
    scene["coverage"]["sensitivity"] = ground_sensitivity(grid, scene, query)
    set_wall_observed_fraction(scene, query)
    scene["assets"] = [{"id": "points", "kind": "point_cloud", "path": "points.ply", "format": "ply", "in_scene_frame": True,
                        "count": int(len(Xw)), "sha256": sha256_file(out_dir / "points.ply")},
                       {"id": "coverage", "kind": "coverage_grid", "path": "coverage.npz", "format": "npz"}]
    scene["provenance"]["runtime_s"] = round(time.time() - t0, 1)
    save_json(scene, out_dir / "scene.json")
    (out_dir / "extract_report.json").write_text(json.dumps(st["report"], indent=1, default=float))
    return score_scene(gt_path, out_dir / "scene.json", out_dir, visibility=visibility, align_info=align)


def transform_from_arg(s: str | None) -> np.ndarray | None:
    if not s:
        return None
    v = json.loads(Path(s).read_text()) if Path(s).exists() else json.loads(s)
    v = np.asarray(v, float)
    return v.reshape(4, 4) if v.shape == (4, 4) else mat4_from_list(v)

