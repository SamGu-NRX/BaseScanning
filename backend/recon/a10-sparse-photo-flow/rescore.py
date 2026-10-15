"""A10: rescore finished rows (score-cloud + C1) without reconstructing again, e.g. after the local tap fix.

    python rescore.py <scene> <quality> <set> <method> [...]

The local harness copy (~/a10/harness) projects GT taps through the recon image's intrinsics (geomscore.py patch),
which matters for A7's iPhone-FOV frames (combo_day); the old score is kept as score_pre_tapfix in the row.
"""

from __future__ import annotations

import json
import sys

import run as R


def main() -> None:
    scene, quality, set_name, methods = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
    for m in methods:
        res = R.HERE / "results" / scene / quality / set_name / f"{m}.json"
        if not res.exists():
            continue
        rep = json.loads(res.read_text())
        if "score_pre_tapfix" in rep or "score" not in rep:
            continue
        out = R.RUNS / scene / quality / set_name / m
        if m in ("colmap", "prior", "prior_old"):
            args = ["--cameras", str(out / "model")] + (["--metric"] if m.startswith("prior") else [])
        elif m in ("cda3", "cpi3", "pi3c"):
            args = ["--cameras", str(out / "cameras.json"), "--metric", "--align", "rotations"]
        else:
            args = ["--cameras", str(out / "cameras.json"), "--metric", "--transform", str(out / "transform.json")]
        rep["score_pre_tapfix"] = {"kd": rep["score"]["kd"], "kd_observed": rep["score"].get("kd_observed")}
        rep["score"] = R.score(scene, out, args, f"A10-{m}-{quality}-{set_name}")
        rep["c1"] = R.criteria(out)
        res.write_text(json.dumps(rep, indent=1, default=float))
        k, o = rep["score"]["kd"], rep["score_pre_tapfix"]["kd"]
        print(f"{scene} {quality} {set_name} {m}: before {o.get('n')} {o.get('median_in')}/{o.get('p90_in')} -> "
              f"after {k.get('n')} {k.get('median_in')}/{k.get('p90_in')}", flush=True)


if __name__ == "__main__":
    main()
