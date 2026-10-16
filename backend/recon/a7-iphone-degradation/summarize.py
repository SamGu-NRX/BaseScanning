"""Tables for the A7 report from results/<scene>/<path>/<cond>.json.

    python summarize.py > results/summary.md

Fixed KD set per path x scene: the key distances (KDs) the path's `dslr_ref` run scored on truly observed
elements; a degraded run that loses one counts it as lost, not as a smaller sample. Two views of it:
- ground-free geometry: opening edge positions, widths, gaps, corner / wall-length KDs, and opening heights
  (top error minus bottom error), none of which depend on where the extractor put the ground;
- ground datum: the median error of the ground-referenced KDs (meter height, sills, heads). The reference
  extractor fits the ground as the best-supported low plane, which flips to a ~0.6 m ledge on the facade
  when the wall base is thinly covered (section 5 of the report).
Bar (2-6 in, as A1 applied it), on the raw fixed set: `in` = all kept and within 6 in with p90 <= 4 in;
`edge` = >= 90 % kept and p90 <= 6 in; `out` otherwise; `broken` = registration < 90 % of the `dslr_ref` count (A1), scorer
failure (meter wall missing), or < 50 % of the fixed KDs kept.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from degrade import CONDITIONS

R = Path(__file__).parent / "results"
ORDER = ["dslr_ref", *CONDITIONS]
PATHS = {"A1": ("A1", "score", "A1 COLMAP + non-LiDAR ARKit priors (metric, rigid)"),
         "pi3": ("pi3", "score", "pi3 on its own poses, scaled by exact camera centres"),
         "pi3gt": ("pi3", "score_gtpose", "pi3 depth placed at exact poses, one global scale"),
         "da3": ("da3", "score", "DA3-Large fed exact poses + frame intrinsics")}
GEOM = ("opening.edge_s", "opening.width", "opening.gap", "corner.s", "wall.length")
DATUM = ("meter.height", "opening.bottom", "opening.top")


def load(scene, folder, cond):
    p = R / scene / folder / f"{cond}.json"
    return json.loads(p.read_text()) if p.exists() else None


def geomscore_path(scene, folder, key, cond) -> Path:
    base = Path.home() / "a7/scores" / scene / folder / cond
    if folder == "A1":
        return base / "score/geomscore.json"
    return base / ("cloud_gtpose" if key == "score_gtpose" else "cloud") / "score/geomscore.json"


def observed_ids(scene, folder, key, cond) -> set[str]:
    g = geomscore_path(scene, folder, key, cond)
    if not g.exists():
        return set()
    rows = json.loads(g.read_text())["key_distances"]["rows"]
    return {r["id"] for r in rows if r["status"] == "scored" and r.get("coverage_truth") == "observed"}


def _stats(e):
    e = np.abs(np.array(e, float))
    return {"n": len(e), "med": float(np.median(e)), "p90": float(np.percentile(e, 90)), "max": float(e.max())} if len(e) else None


def split(rows: dict, ids: set[str]):
    raw = [rows[i] for i in ids if rows.get(i) is not None]
    geom = [rows[i] for i in ids if i.startswith(GEOM) and rows.get(i) is not None]
    for i in ids:
        if i.startswith("opening.top["):
            b = i.replace("opening.top[", "opening.bottom[")
            if b in ids and rows.get(i) is not None and rows.get(b) is not None:
                geom.append(rows[i] - rows[b])
    datum = [rows[i] for i in ids if i.startswith(DATUM) and rows.get(i) is not None]
    return raw, geom, (float(np.median(datum)) if datum else None)


def verdict(folder, rep, sc, kept, n, st, ref_registered=None):
    if rep is None:
        return "—"
    if folder == "A1" and rep.get("registered", 0) < 0.9 * (ref_registered or rep["images"]):
        return "broken (registration)"
    if not sc:
        if rep.get("score_skipped"):
            return "surface only"
        return "broken (no meter wall)" if "meter clicks hit no wall" in json.dumps(rep) else "broken (scorer)"
    if n == 0 or kept < 0.5 * n:
        return "broken (KDs lost)"
    if kept == n and st["max"] <= 6 and st["p90"] <= 4:
        return "in"
    if kept >= 0.9 * n and st["p90"] <= 6:
        return "edge"
    return "out"


def fmt(st):
    return f"{st['med']:.1f} / {st['p90']:.1f} / {st['max']:.1f}" if st else "—"


def table(scene, path):
    folder, key, title = PATHS[path]
    ids = observed_ids(scene, folder, key, "dslr_ref")
    out = [f"\n### {scene}: {title}\n\nFixed set: {len(ids)} KDs observed in `dslr_ref`. Errors in inches (median / p90 / max of |error|).\n"]
    head = "| Condition | " + ("Registered | Keypts (med) | Verified pairs | Centre RMS | Scale err | " if folder == "A1" else
                             "Pose centre RMS (Sim3) | Rot err (med) | ")
    out.append(head + "KDs kept | Raw fixed set | Ground-free geometry | Ground datum offset | Surface (ICP) med / p90, compl 5 cm | Bar (raw) |")
    out.append("|" + "---|" * (head.count("|") + 5))
    rows_out = {}
    for c in ORDER:
        rep = load(scene, folder, c)
        if rep is None:
            continue
        sc = rep.get(key)
        rows = (sc or {}).get("rows") or {}
        raw, geom, datum = split(rows, ids)
        kept = len([i for i in ids if rows.get(i) is not None])
        st_raw, st_geom = _stats(raw), _stats(geom)
        ref = load(scene, folder, "dslr_ref") if folder == "A1" else None
        v = verdict(folder, rep, sc, kept, len(ids), st_raw, ref and ref.get("registered"))
        su = rep.get("surface_gtpose" if key == "score_gtpose" else "surface")
        wf = f"{su['acc_med_in']:.1f} / {su['acc_p90_in']:.1f}, {100 * su['compl_5cm']:.0f} %" if su and "acc_med_in" in su else "—"
        dat = f"{datum:+.1f}" if datum is not None else "—"
        if folder == "A1":
            reg = f"{rep.get('registered', 0)}/{rep['images']}"
            rms = f"{100 * rep['centre_rms_rigid_m']:.1f} cm" if "centre_rms_rigid_m" in rep else "—"
            se = f"{rep['scale_error_pct']:+.2f} %" if "scale_error_pct" in rep else "—"
            pre = f"| {c} | {reg} | {rep['keypoints_median']:.0f} | {rep['inlier_pairs']} | {rms} | {se} | "
        else:
            pre = f"| {c} | {100 * rep['pose_centre_rms_sim3_m']:.1f} cm | {rep['pose_rot_err_median_deg']:.2f}° | "
        out.append(pre + f"{kept}/{len(ids)} | {fmt(st_raw)} | {fmt(st_geom)} | {dat} | {wf} | {v} |")
        rows_out[c] = {"verdict": v, "raw": st_raw, "geom": st_geom, "datum": datum, "kept": kept, "n": len(ids), "surface": su}
    return "\n".join(out), rows_out


if __name__ == "__main__":
    print("# A7 degradation sweep: generated tables (python summarize.py)\n")
    print(__doc__.split("\n\n", 1)[1])
    allrows = {}
    for path in PATHS:
        for scene in ("facade", "electro"):
            t, r = table(scene, path)
            allrows[f"{scene}/{path}"] = r
            print(t)
    (R / "summary.json").write_text(json.dumps(allrows, indent=1))
