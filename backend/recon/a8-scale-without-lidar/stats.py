"""Aggregate the reference-segment measurements into the error terms used by combine.py.

    python stats.py  ->  results/segments_summary.{json,md}

Terms (all real, measured on the three scenes):
  rec_err     : recon length x s_true vs laser length for the same snapped pixels (ETH3D only; barn truth is the
                laser-aligned T&T cameras, so it has no independent recon term).
  view spread : the same physical object measured from different photos (independent snapping), relative SD of
                the metric length; this is the click / edge-choice error of a reference.
Rows whose edge snap found a line on under half of the samples are dropped (edge not found).
"""

from __future__ import annotations

import json
from collections import defaultdict

import numpy as np

from common import HERE, IN, dump

MIN_SNAP = 0.5


def main() -> None:
    rows = []
    for sc in ("facade", "electro", "barn"):
        d = json.loads((HERE / "results" / sc / "segments.json").read_text())
        for r in d["rows"]:
            r["scene"] = sc
            rows.append(r)
    good = [r for r in rows if "error" not in r and min(r["snap_inliers"]) >= MIN_SNAP]
    dropped = [(r["scene"], r["id"], r["view"], r.get("error", f"snap {r.get('snap_inliers')}")) for r in rows if r not in good]
    rec = np.array([r["rec_err_pct"] for r in good if r["scene"] != "barn"])
    by = defaultdict(list)
    for r in good:
        by[(r["scene"], r["id"])].append(r)
    objs = []
    for (sc, oid), rs in by.items():
        auto_counts = [r["n"] for r in rs]
        if rs[0]["type"] == "brick_courses":
            # Course count from the laser length (what a correct close-up count gives); the automatic
            # counter's hit rate is reported separately.
            course = np.median([r["L_laser"] / 27 for r in rs if abs(r["L_laser"] - 2.046) < 0.02])
            for r in rs:
                r["n_auto"], r["n"] = r["n"], int(round(r["L_laser"] / course))
        med = np.median([r["L_laser"] / r["n"] for r in rs])
        gross = [r for r in rs if abs(r["L_laser"] / r["n"] / med - 1) > 0.25]
        typ = rs[0]["type"]
        rs = [r for r in rs if r not in gross]
        if not rs:
            objs.append({"scene": sc, "id": oid, "type": typ, "views": 0, "gross_views": len(gross), "note": "views disagree >25 %"})
            continue
        unit = np.array([r["L_laser"] / r["n"] for r in rs])
        ns = sorted({r["n"] for r in rs})
        objs.append({"scene": sc, "id": oid, "type": rs[0]["type"], "views": len(rs), "counts": ns, "gross_views": len(gross),
                     "auto_counts": auto_counts,
                     "unit_in_mean": float(unit.mean() / IN), "unit_in_min": float(unit.min() / IN), "unit_in_max": float(unit.max() / IN),
                     "view_sd_pct": float(100 * unit.std(ddof=1) / unit.mean()) if len(rs) > 1 else None,
                     "range_pct": float(100 * (unit.max() - unit.min()) / unit.mean()),
                     "px_median": float(np.median([r["px"] for r in rs])), "depth_m": float(np.median([r["depth_m"] for r in rs])),
                     "rec_err_pct": [round(r["rec_err_pct"], 2) for r in rs] if sc != "barn" else None})
    out = {"rec_err_pct": {"n": int(len(rec)), "mean": float(rec.mean()), "sd": float(rec.std(ddof=1)),
                           "min": float(rec.min()), "max": float(rec.max())},
           "objects": objs, "dropped": dropped}
    dump(HERE / "results" / "segments_summary.json", out)
    lines = ["| Scene | Object | Type | Views (+gross) | Count(s) | Unit size, in (mean; min–max) | View-to-view SD | px | Depth m | Recon vs laser % |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for o in objs:
        if not o["views"]:
            lines.append(f"| {o['scene']} | {o['id']} | {o['type']} | 0 (+{o['gross_views']}) | – | views disagree by > 25 % | – | – | – | – |")
            continue
        sd = f"{o['view_sd_pct']:.2f} %" if o["view_sd_pct"] is not None else "–"
        rec_s = ", ".join(f"{v:+.2f}" for v in o["rec_err_pct"]) if o["rec_err_pct"] else "camera truth"
        lines.append(f"| {o['scene']} | {o['id']} | {o['type']} | {o['views']} (+{o['gross_views']}) | {o['counts']} | {o['unit_in_mean']:.2f}; {o['unit_in_min']:.2f}–{o['unit_in_max']:.2f} | {sd} | {o['px_median']:.0f} | {o['depth_m']:.1f} | {rec_s} |")
    lines.append("")
    lines.append(f"Recon vs laser, same pixels (ETH3D, {len(rec)} measurements): mean {rec.mean():+.2f} %, SD {rec.std(ddof=1):.2f} %, range {rec.min():+.2f} to {rec.max():+.2f} %.")
    lines.append(f"Dropped (edge not found or plane fit failed): {len(dropped)} of {len(rows)}.")
    (HERE / "results" / "segments_summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
