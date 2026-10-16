"""Shared-scorer runs on the ETH3D facade: A1's scale-free incremental SfM cloud, scaled to metres by each
scale method's estimate, scored with `score-cloud --metric` (rigid alignment, so the scale error stays in
every key distance).

    python score_scaled.py NAME SCALE_ERR_PCT [NAME SCALE_ERR_PCT ...]
    -> results/facade/scorer/<NAME>/geomscore.{json,md}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from common import HERE, STORE, dump, load_scene

sys.path.insert(0, str(STORE / "internal/eval-harness"))
from evalharness.geomscore import score_cloud  # noqa: E402
from evalharness.sceneio import write_ply  # noqa: E402

GT = Path.home() / "a8/data/eval-harness/facade-gt/scene.json"


def main() -> None:
    sc = load_scene("facade", laser=False)
    args = sys.argv[1:]
    for name, e in zip(args[::2], args[1::2]):
        s = sc.s_true * (1 + float(e) / 100)
        out = HERE / "results/facade/scorer" / name
        out.mkdir(parents=True, exist_ok=True)
        ply = Path.home() / "a8/work/facade" / f"scaled_{name}.ply"
        write_ply(ply, (sc.rec_pts * s).astype(np.float32))
        cams = [{"image": c.name, "R_cw": c.R.tolist(), "t_cw": (c.t * s).tolist(),
                 "intrinsics": [c.K[0, 0], c.K[1, 1], c.K[0, 2], c.K[1, 2]], "w": c.w, "h": c.h} for c in sc.rec_cams.values()]
        cj = out / "cameras.json"
        cj.write_text(json.dumps(cams))
        r = score_cloud(str(GT), str(ply), str(cj), str(out), metric=True, producer=f"A8-{name}")
        kd = r.get("key_distances", {}).get("stats", r.get("key_distances", {}))
        print(name, e, json.dumps(kd)[:400], flush=True)


if __name__ == "__main__":
    main()
