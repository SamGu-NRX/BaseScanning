"""A10: rescore a finished run at a no-tool scale instead of true scale.

    python rescale.py <scene> <quality> <set> <method> <tag>=<rel_err> [...]
    e.g.  rescale.py facade dslr_ref N60_guided pi3 weak_med=0.017 weak_p90=0.047

The run's cameras and cloud are first put at true metric scale (Sim(3) of its camera centres to the GT
centres; for `prior` runs the ARKit scale is replaced), then multiplied by (1 + rel_err) and scored
rigidly (--metric, rotation-aware alignment), so the applied error stays in every number. C1 runs on the
result. Error sizes come from A8 3.5 (no homeowner action: weak references = ARKit + meter from a house
photo + door + learned depth, 1.7 % median / 4.7 % p90 absolute; meter close-up alone 0.6 % / 7.6 %).
Positive errors are applied because they are C1's worst case (they widen gaps between openings).
Output: results/<scene>/<quality>/<set>/<method>.scale.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pycolmap

import run as R


def load_cams(out: Path, method: str) -> list[dict]:
    if method in ("colmap", "prior", "prior_old"):
        rec = pycolmap.Reconstruction(str(out / "model"))
        cams = []
        for im in rec.images.values():
            if not im.has_pose:
                continue
            cw = im.cam_from_world()
            cam = rec.cameras[im.camera_id]
            cams.append({"image": Path(im.name).stem, "R_cw": cw.rotation.matrix().tolist(), "t_cw": list(map(float, cw.translation)),
                         "intrinsics": list(map(float, cam.params[:4])), "w": cam.width, "h": cam.height})
        return cams
    return json.loads((out / "cameras.json").read_text())


def read_ply(path: Path) -> np.ndarray:
    raw = path.read_bytes()
    i = raw.index(b"end_header\n") + len(b"end_header\n")
    return np.frombuffer(raw[i:], np.float32).reshape(-1, 3).astype(np.float64)


def main() -> None:
    scene, quality, set_name, method = sys.argv[1:5]
    errs = {a.split("=")[0]: float(a.split("=")[1]) for a in sys.argv[5:]}
    out = R.RUNS / scene / quality / set_name / method
    cams = load_cams(out, method)
    P = read_ply(out / "cloud.ply")
    gt = R.gt_c2w(scene)
    C = np.array([-np.array(c["R_cw"]).T @ np.array(c["t_cw"]) for c in cams])
    G = np.array([gt[c["image"]][:3, 3] for c in cams])
    s_true, _, _ = R.umeyama(C, G)
    resf = R.HERE / "results" / scene / quality / set_name / f"{method}.scale.json"
    res = json.loads(resf.read_text()) if resf.exists() else {}
    for tag, e in errs.items():
        if tag in res:
            continue
        k = s_true * (1 + e)
        dest = out / f"scale_{tag}"
        dest.mkdir(exist_ok=True)
        R.write_ply(P * k, dest / "cloud.ply")
        sc = [dict(c, t_cw=(np.array(c["t_cw"]) * k).tolist()) for c in cams]
        (dest / "cameras.json").write_text(json.dumps(sc))
        rep = {"rel_err_applied": e, "true_scale_factor": s_true}
        rep["score"] = R.score(scene, dest, ["--cameras", str(dest / "cameras.json"), "--metric", "--align", "rotations"],
                               f"A10-{method}-{quality}-{set_name}-{tag}", cloud=dest / "cloud.ply")
        rep["c1"] = R.criteria(dest)
        res[tag] = rep
        resf.write_text(json.dumps(res, indent=1, default=float))
        kd = rep["score"]["kd"]
        print(f"{scene} {quality} {set_name} {method} {tag}: kd n={kd.get('n')} med={kd.get('median_in')} p90={kd.get('p90_in')} "
              f"scale={rep['score']['cloud_scale_error_pct']} c1={rep['c1']['g80'].get('decision')} dec={rep['c1']['g80'].get('decidable_share')}",
              flush=True)


if __name__ == "__main__":
    main()
