"""Compact per-scene tables from a2ff.score JSON: python -m a2ff.report_tables phase1 [phase2 ...]

One row per (method, variant), one cell per scene: 1-3 m pairs median / p90 inches, % within 3 in,
scale error; for a range and cohort. Posthoc draws of a profile are collapsed to their range.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict

import numpy as np

from a2ff.score import RESULTS

SCENES = ("delivery_area", "electro", "facade")


def cell(c: dict | None, bin_key: str, observed: bool = False) -> tuple | None:
    if not c:
        return None
    src = c.get("well_observed") if observed else c
    if not src:
        return None
    a = src["none"].get(bin_key)
    t = src["one_known_distance"].get(bin_key)
    if not a:
        return None
    return a["median_in"], a["p90_in"], a.get("within_3in_pct"), a.get("scale_error_pct"), t["median_in"], t["p90_in"], \
        c.get("well_observed_pairs_pct")


def f(x: float) -> str:
    return "fail" if not np.isfinite(x) else f"{x:.1f}"


def main() -> None:
    tags = sys.argv[1:] or ["phase1"]
    data: dict = defaultdict(dict)
    for tag in tags:
        for scene, per_range in json.loads((RESULTS / f"{tag}.json").read_text()).items():
            for rng, per_m in per_range.items():
                for m, per_n in per_m.items():
                    for n, per_v in per_n.items():
                        for v, r in per_v.items():
                            data[(rng, m, n, v)][scene] = r
    out = []
    for rng in ("within 6 m", "all points"):
        for cohort in ("surface interior", "vertical interior", "edges"):
            for bk in ("1-3m", "3-10m"):
                out += [f"## {rng}, {cohort}, {bk} pairs", "",
                        "Cell: median / p90 in, % within 3 in, scale error; taped median / p90 in. "
                        "Posthoc draws 0-2 collapsed to min-max.", "",
                        "| Method | Views | Variant | " + " | ".join(SCENES) + " |",
                        "| --- | --- | --- | " + " | ".join("---" for _ in SCENES) + " |"]
                groups: dict = defaultdict(list)
                for (r, m, n, v), per_s in data.items():
                    if r != rng:
                        continue
                    key = re.sub(r"\.draw\d$", ".draws", v)
                    groups[(m, n, key)].append(per_s)
                for (m, n, key), items in sorted(groups.items()):
                    cells = []
                    for s in SCENES:
                        vals = [cell(it.get(s, {}).get(cohort), bk) for it in items]
                        vals = [x for x in vals if x]
                        if not vals:
                            cells.append("n/a")
                            continue
                        if len(vals) == 1:
                            md, p9, w3, sc, tm, tp, wo = vals[0]
                            cells.append(f"{f(md)} / {f(p9)}, {w3}%, {sc:+.1f}%; tape {f(tm)} / {f(tp)}")
                        else:
                            md = [x[0] for x in vals]
                            p9 = [x[1] for x in vals]
                            w3 = [x[2] for x in vals]
                            sc = [x[3] for x in vals]
                            cells.append(f"{f(min(md))}-{f(max(md))} / {f(min(p9))}-{f(max(p9))}, "
                                         f"{min(w3)}-{max(w3)}%, {min(sc):+.1f}..{max(sc):+.1f}%")
                    out.append(f"| {m} | {n} | {key} | " + " | ".join(cells) + " |")
                out.append("")
    md = "\n".join(out) + "\n"
    name = "tables_" + "_".join(tags)
    (RESULTS / f"{name}.md").write_text(md)
    print(f"wrote {RESULTS / name}.md")


if __name__ == "__main__":
    main()
