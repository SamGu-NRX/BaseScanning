"""Markdown table of the shared scorer's results for every A1 facade run.

    python summarize.py > summary.md
"""

from __future__ import annotations

import json

from common import STORE

RES = STORE / "internal/eval-harness/results/eth3d-facade/A1-colmap"
ORDER = ["gt", "arkit_lidar_modern_s0_fixed", "arkit_lidar_modern_s0_prior", "arkit_nonlidar_modern_s0_fixed",
         "arkit_nonlidar_modern_s0_prior", "incremental", "global"]


def row(run: str, kind: str) -> str | None:
    p = RES / f"{run}_{kind}" / "geomscore.json"
    if not p.exists():
        return None
    g = json.loads(p.read_text())
    kd = g["key_distances"]["overall"]
    counts = g["key_distances"].get("status_counts") or {}
    comp = g["completeness"]
    c10 = lambda r: comp[r]["declared"]["within_10cm"]
    obs = g["key_distances"].get("on_truly_observed_elements") or {"n": 0, "median_in": None, "p90_in": None}

    def fo(r):
        v = comp[r].get("coverage", {}).get("false_observed_by_rule")
        return "n/a" if v is None else f"{v:.1%}"

    sc = g["scale"]
    al = sc.get("alignment", {})
    scale = al.get("cloud_scale_error_pct") if al.get("metric_declared") else None
    kds = sc.get("scale_error_pct")
    kds = "n/a" if kds is None else f"{kds:+.2f} %"
    return (f"| {run} | {kind} | {counts.get('scored', kd['n'])} / {counts.get('missing', '?')} | {kd['median_in']} / {kd['p90_in']} / {kd['max_in']} | "
            f"{kd['share_le_2in']:.0%} / {kd['share_le_4in']:.0%} / {kd['share_le_6in']:.0%} | {kd['calibration']:.0%} | "
            f"{'' if scale is None else f'{scale:+.2f} %'} | {kds} | {g['anchor']['icp_translation_norm_m']:.3f} | "
            f"{c10('meter_wall'):.0%} | {c10('wall_face'):.0%} | {c10('ground_strip'):.0%} | {c10('overhead'):.0%} | "
            f"{obs['n']}: {obs['median_in']} / {obs['p90_in']} | {len(g['key_distances'].get('false_observed_claims', []))} | "
            f"{fo('wall_face')} / {fo('ground_strip')} |")


def main():
    print("| Run | Cloud | KDs scored / missing | KD \\|err\\| median / p90 / max (in) | ≤2 / 4 / 6 in | Calibration | "
          "Cloud scale err | KD scale err | Anchor ICP (m) | Meter wall ≤10 cm | Wall face ≤10 cm | Ground strip ≤10 cm | Overhead ≤10 cm | "
          "KDs on truly observed elements: n: median / p90 (in) | False-observed KD claims | False-observed by rule: wall face / ground strip |")
    print("|" + "---|" * 16)
    for kind in ("dense", "sparse"):
        for r in ORDER:
            x = row(r, kind)
            if x:
                print(x)


if __name__ == "__main__":
    main()
