"""Score A3 fused meshes on ETH3D facade with the shared geometry scorer (`evalharness.cli score-cloud`).

    python harness.py <eval tag> [<eval tag> ...]      # tags of WORK/eval/facade/<tag>.json runs

Writes the mesh vertices as a binary PLY and the cameras the fusion used (the pose mode's poses,
so noisy runs carry their drift) as the scorer's JSON, then runs score-cloud with --metric (rigid
alignment: A3 claims metric scale, so the scale error stays in). Results go to
internal/eval-harness/results/eth3d-facade/A3-<tag>/.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import open3d as o3d

from common import WORK  # noqa: I001
from fuse import FUSED, _scene
from poses import poses

HARNESS = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/eval-harness")
GT = WORK / "facade-gt" / "scene.json"
VENV_PY = Path.home() / "a3" / "venv" / "bin" / "python"


def export(tag: str) -> Path:
    res = json.loads((WORK / "eval" / "facade" / f"{tag}.json").read_text())
    out = WORK / "harness" / tag
    out.mkdir(parents=True, exist_ok=True)
    mesh = o3d.io.read_triangle_mesh(str(FUSED / "facade" / f"{tag}.ply"))
    pc = o3d.geometry.PointCloud(mesh.vertices)
    o3d.io.write_point_cloud(str(out / "cloud.ply"), pc, write_ascii=False)
    T, _ = poses("facade", res["poses"])
    sc = _scene("facade")
    cams = []
    for n, Tn in sorted(T.items()):
        K = sc.K(n)
        h = round(1024 * sc.views[n].height / sc.views[n].width)
        R_cw = Tn[:3, :3].T
        cams.append({"image": n, "R_cw": R_cw.tolist(), "t_cw": (-R_cw @ Tn[:3, 3]).tolist(),
                     "intrinsics": [K[0, 0], K[1, 1], K[0, 2] + 0.5, K[1, 2] + 0.5], "w": 1024, "h": h})
    (out / "cameras.json").write_text(json.dumps(cams))
    return out


def score(tag: str) -> dict:
    out = export(tag)
    dest = HARNESS / "results" / "eth3d-facade" / f"A3-{tag}"
    subprocess.run([str(VENV_PY), "-m", "evalharness.cli", "score-cloud", "--gt", str(GT),
                    "--cloud", str(out / "cloud.ply"), "--cameras", str(out / "cameras.json"), "--metric",
                    "--producer", f"A3-{tag}", "--out", str(dest),
                    # The visibility oracle exited with an error on these clouds (harness as of 17:50).
                    "--no-visibility"], cwd=HARNESS, check=True)
    return json.loads((dest / "geomscore.json").read_text())


if __name__ == "__main__":
    for t in sys.argv[1:]:
        r = score(t)
        kd = r["key_distances"]["overall"]
        print(t, json.dumps({"kd": kd, "status": r["key_distances"]["status_counts"],
                             "scale": {k: v for k, v in r["scale"].items() if k != "alignment"}}, default=str))
