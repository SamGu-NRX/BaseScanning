"""Markdown tables from WORK/eval/<scene>/*.json (A3 runs).

    python report.py electro [facade ...]  > tables.md
"""

from __future__ import annotations

import json
import sys

import numpy as np

from common import WORK

P = " | predicted"


def cell(res, block, cohort, src, b, rng="within 6 m"):
    x = res[block].get(rng, {}).get(cohort, {}).get(src, {}).get(b)
    if not x or not x.get("pairs"):
        return "n/a"
    p90 = "fail" if not np.isfinite(x["p90_in"]) else f"{x['p90_in']:.1f}"
    return f"{x['median_in']:.1f} / {p90}"


def cov(res, block, rng="within 6 m"):
    x = res[block].get(rng, {}).get("surface interior", {}).get("none", {}).get("1-3m")
    return f"{100 - x['failed_pct']:.0f}%" if x else "n/a"


def scale(res, block, rng="within 6 m"):
    x = res[block].get(rng, {}).get("surface interior" + P, {}).get("none", {}).get("3-10m", {})
    v = x.get("scale_error_pct")
    return "n/a" if v is None else f"{v:+.1f}%"


def rows(scene: str) -> list[str]:
    out = [
        f"### {scene}: within 6 m of the seed camera, inches median / p90 on predicted points",
        "",
        "| Model | Poses | Scale | Fused: pairs predicted | Fused raw 1-3 m | Fused raw 3-10 m | Fused vertical 1-3 m | "
        "Fused edges 1-3 m | Fused scale err | Single: predicted | Single raw 1-3 m | Single, one tape 1-3 m | "
        "Acc ≤10 cm | Comp ≤10 cm |",
        "| --- |" * 1 + " --- |" * 13,
    ]
    for f in sorted((WORK / "eval" / scene).glob("*.json")):
        r = json.loads(f.read_text())
        s = r["surface"]
        out.append(
            f"| {r['model']} | {r['poses']} | {r['scale']}{'' if r.get('voxel', 0.04) == 0.04 else ' v' + str(r['voxel'])}"
            f"{'' if r.get('trunc_vox', 4) == 4 else ' t' + str(r['trunc_vox'])} "
            f"| {cov(r, 'pairs_fused')} | {cell(r, 'pairs_fused', 'surface interior' + P, 'none', '1-3m')} "
            f"| {cell(r, 'pairs_fused', 'surface interior' + P, 'none', '3-10m')} "
            f"| {cell(r, 'pairs_fused', 'vertical interior' + P, 'none', '1-3m')} "
            f"| {cell(r, 'pairs_fused', 'edges' + P, 'none', '1-3m')} | {scale(r, 'pairs_fused')} "
            f"| {cov(r, 'pairs_single')} | {cell(r, 'pairs_single', 'surface interior' + P, 'none', '1-3m')} "
            f"| {cell(r, 'pairs_single', 'surface interior' + P, 'one_known_distance', '1-3m')} "
            f"| {s.get('accuracy_10cm', 0):.0%} | {s.get('completeness_10cm', 0):.0%} |"
        )
    return out


if __name__ == "__main__":
    for sc in sys.argv[1:]:
        print("\n".join(rows(sc)) + "\n")
