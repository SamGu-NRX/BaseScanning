"""Real-measurement check of the typed-object fusion on the one US building with laser-registered truth
(Tanks and Temples barn, A8's measurements in true units): garage-door width, man-door height, lap
siding reveal. Their measured sizes are fixed (real); only ARKit's scale draw varies (A1 bootstrap).

    python barn_typed.py  ->  results/barn_typed.json
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
A8 = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "a8-scale"
sys.modules.setdefault("pycolmap", types.ModuleType("pycolmap"))
sys.argv = sys.argv[:1]
sys.path.insert(0, str(A8))
sys.path.insert(0, str(HERE))
import no_tape_mc as M  # noqa: E402

seg = json.load(open(A8 / "results/segments_summary.json"))
meas = {r["id"]: r["unit_in_mean"] for r in seg["objects"] if r["scene"] == "barn" and r.get("unit_in_mean")}
print(meas)
IN, FT, N = M.IN, M.FT, M.N


def fixed(name, value_in, ref_in, sizes_in, weights, sd):
    o = np.full(N, np.log(ref_in / value_in))
    return M.Src(name, o, np.ones(N, bool), sd, offsets=np.log(ref_in / np.asarray(sizes_in)), weights=np.asarray(weights))


# garage: single-door SKUs (aspect cue unavailable: the height snap failed on the roll-up door)
gw = [96, 108, 120]
garage = fixed("garage width", meas["garage_w"], 96, gw, [0.3, 0.55, 0.15], 0.011)
door = fixed("man door height (outline)", meas["mandoor_h"], 80, [80, 78, 84, 96], [0.8, 0.05, 0.1, 0.05], 0.016)
ex = [4, 4.5, 5, 6, 7, 8]
siding = fixed("siding reveal (wood/fibre, typed)", meas["siding_L"], 6, ex, [.2, 0, .3, .3, .1, .1], 0.011)
meter = M.meter("exact")
out = {"measured_in": meas}
for oc in (0.0, 0.2):
    M.OFFCAT["w"] = oc
    for prof, sd in (("nonlidar_modern", 0.03), ("nonlidar_2018", 0.10)):
        ark = M.arkit(prof, sd)
        for combo, srcs in {"ARKit only": [ark], "ARKit + garage": [ark, garage], "ARKit + door": [ark, door],
                            "ARKit + siding": [ark, siding], "ARKit + garage + door + siding": [ark, garage, door, siding],
                            "ARKit + meter exact (sim.)": [ark, meter],
                            "ARKit + meter exact (sim.) + garage + door + siding": [ark, meter, garage, door, siding]}.items():
            e, b, cov = M.fuse(srcs)
            a = np.abs(e)
            out[f"offcat {oc} | {prof} | {combo}"] = {"median_abs_pct": 100 * float(np.median(a)), "p90_abs_pct": 100 * float(np.percentile(a, 90)),
                                       "bias_pct": 100 * float(np.median(e)), "bound95_median_pct": 100 * float(np.median(b)),
                                       "coverage95": float(cov.mean())}
            print(oc, prof, combo, {k: round(v, 2) for k, v in out[f"offcat {oc} | {prof} | {combo}"].items()})
json.dump(out, open(HERE / "results/barn_typed.json", "w"), indent=1)
