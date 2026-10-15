"""Scene v0 that indexes C2 spots on a photo-built model: the site's wall chain (GT ids, rescaled
about the meter by the model's scale error, the same way the C2 labels are), with the recon's own
keyframes, point cloud and coverage grid, so every coverage query answers from the photos.

    python c2_index_scene.py --gt GT/scene.json --e2e E2E_DIR --out E2E_DIR/c2/index_scene.json
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path

import numpy as np


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", type=Path, required=True)
    ap.add_argument("--e2e", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    gt = json.loads(a.gt.read_text())
    rc = json.loads((a.e2e / "scene.json").read_text())
    e = json.loads((a.e2e / "e2e_info.json").read_text())["applied_scale_error_pct"] / 100
    f = 1 + e
    sc = copy.deepcopy(gt)

    def s(v):
        return round(float(v) * f, 4)

    def p2(q):
        return [s(q[0]), s(q[1])]

    for w in sc["walls"]:
        w["baseline_m"] = [p2(q) for q in w["baseline_m"]]
        if "s_range_m" in w:
            w["s_range_m"] = [s(v) for v in w["s_range_m"]]
        w.pop("polygon_m", None)
        w.pop("observed_fraction", None)
    for c in sc["chain"]["corners"]:
        c["point_m"], c["s_m"] = p2(c["point_m"]), s(c["s_m"])
    for end in sc["chain"]["ends"].values():
        if "point_m" in end:
            end["point_m"] = p2(end["point_m"])
        if "s_m" in end:
            end["s_m"] = s(end["s_m"])
    rel = os.path.relpath(a.e2e, a.out.parent)
    sc["keyframes"] = rc["keyframes"]
    sc["assets"] = [dict(x, path=f"{rel}/{x['path']}") for x in rc["assets"] if x["kind"] in ("point_cloud", "coverage_grid")]
    sc["coverage"] = copy.deepcopy(rc["coverage"])
    sc["coverage"]["grid"] = dict(sc["coverage"]["grid"], asset=sc["coverage"]["grid"]["asset"])
    sc["coverage"].pop("bands", None)
    sc["coverage"].pop("sensitivity", None)
    sc["scene_id"] = rc["scene_id"] + "/c2-index"
    sc["provenance"] = copy.deepcopy(rc["provenance"])
    sc["provenance"]["notes"] = sc["provenance"].get("notes", []) + [
        "C2 index scene: GT wall chain rescaled about the meter by the model's scale error; keyframes, points and coverage from the photo model."]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(sc, indent=1))
    print(a.out)


if __name__ == "__main__":
    main()
