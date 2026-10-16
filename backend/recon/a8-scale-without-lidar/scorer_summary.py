"""Summarise the facade shared-scorer runs by key-distance length.

    python scorer_summary.py -> results/facade/scorer/summary.md
"""

from __future__ import annotations

import json

import numpy as np

from common import HERE

ORDER = ["oracle", "tape", "plus1p5", "minus1p5", "arkit_modern_med", "arkit_modern_p90", "arkit_2018_med",
         "arkit_2018_p90", "brick_modular", "moge2", "metric3d"]
APPLIED = {"oracle": 0, "tape": 0.6, "plus1p5": 1.5, "minus1p5": -1.5, "arkit_modern_med": -2.4, "arkit_modern_p90": -5.6,
           "arkit_2018_med": -7.0, "arkit_2018_p90": -17.5, "brick_modular": -10.0, "moge2": -12.5, "metric3d": 20.3}


def main() -> None:
    root = HERE / "results/facade/scorer"
    lines = ["| Run | applied scale err | KDs scored | all KDs median / p90 (in) | KDs < 1 m median | KDs 1–3 m median / max | KDs > 3 m: n, median / max (in) | scorer scale err |",
             "|---|---|---|---|---|---|---|---|"]
    for name in ORDER:
        p = root / name / "geomscore.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        rows = [r for r in d["key_distances"]["rows"] if r["status"] == "scored" and r.get("unit") == "m"]
        gt = np.array([r["gt"] for r in rows])
        err = np.array([abs(r["error"]) for r in rows])
        o = d["key_distances"]["overall"]
        s = lambda m: err[m]
        short, mid, long_ = gt < 1, (gt >= 1) & (gt <= 3), gt > 3
        lt = f"{long_.sum()}, {np.median(s(long_)):.1f} / {s(long_).max():.1f}" if long_.any() else "0"
        lines.append(f"| {name} | {APPLIED[name]:+.1f} % | {o['n']} | {o['median_in']:.2f} / {o['p90_in']:.1f} | "
                     f"{np.median(s(short)):.1f} | {np.median(s(mid)):.1f} / {s(mid).max():.1f} | {lt} | {d['scale'].get('scale_error_pct', float('nan')):+.2f} % |")
    (root / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
