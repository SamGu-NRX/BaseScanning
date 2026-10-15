"""Rescore existing A7 results with the current harness copy (after the perfect-click intrinsics patch).

    python rescore.py [<scene>/<path>/<cond> ...]     # default: every results/*/*/*.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import a1run
from ffrun import HARNESS, SCORES, summarize

R = Path(__file__).parent / "results"


def rescore(f: Path) -> None:
    scene, path, cond = f.parent.parent.name, f.parent.name, f.stem
    rep = json.loads(f.read_text())
    if path == "A1":
        if rep.get("registered", 0) >= 3:
            rep["score"] = a1run.score(scene, cond)
    else:
        gtp = Path.home() / f"a7/gt/{scene}-gt/scene.json"
        for folder, key in (("cloud", "score"), ("cloud_gtpose", "score_gtpose")):
            dest = SCORES / scene / path / cond / folder
            if not (dest / "cloud.ply").exists():
                continue
            cmd = [sys.executable, "-m", "evalharness.cli", "score-cloud", "--gt", str(gtp), "--cloud", str(dest / "cloud.ply"),
                   "--cameras", str(dest / "cameras.json"), "--metric", "--transform", str(dest / "transform.json"),
                   "--producer", f"A7-{path}-{folder}-{cond}", "--out", str(dest / "score")]
            subprocess.run(cmd, check=True, cwd=HARNESS, stdout=subprocess.DEVNULL)
            rep[key] = summarize(dest / "score" / "geomscore.json")
    rep["rescored"] = "perfect-click intrinsics patch"
    f.write_text(json.dumps(rep, indent=1, default=float))
    print("rescored", f, flush=True)


if __name__ == "__main__":
    files = [R / f"{a}.json" for a in sys.argv[1:]] or sorted(R.glob("*/*/*.json"))
    for f in files:
        try:
            rescore(f)
        except Exception as e:  # noqa: BLE001
            rep = json.loads(f.read_text())
            rep.pop("score", None)
            rep["score_error"] = repr(e)
            rep["rescored"] = "perfect-click intrinsics patch (scorer failed)"
            f.write_text(json.dumps(rep, indent=1, default=float))
            print("score failed", f, repr(e)[:200], flush=True)
