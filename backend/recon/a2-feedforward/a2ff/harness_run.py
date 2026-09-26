"""Score A2 models through the shared geometry scorer (internal/eval-harness, `score-cloud`).

    python -m a2ff.harness_run --model pi3 --modes images
    python -m a2ff.harness_run --score-only

Real images, real laser truth; ARKit poses synthetic (packet generator).

Each model runs once on the 16 facade frames that face the scored meter-wall elements (w1 windows,
the invented meter, the w1/w2/w3 corners and the door): DSC_0357-0366, 0394, 0395, 0419-0422. The
run's depth maps (every finite depth, 1024 px grid) are back-projected into one PLY with the run's
cameras, then scored three ways:

- `metric`: the cloud as the model output it, aligned rigidly (`--metric`), for models that claim
  metric scale (MapAnything, and any run fed ARKit-like poses);
- `shape`: similarity-aligned (scale corrected by the harness from the GT cameras): shape only;
- `posthoc-<setting>`: images-only runs moved into the ARKit-like frame by a Sim(3) fit of their
  camera centres to that setting's centres, then aligned rigidly: metric scale from the phone's
  poses after the fact.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np

import a2ff.run as runmod
from a2ff.common import PRED_ROOT, ROOT, load_poses, umeyama_sim3

MEMBERS = ["DSC_0361", "DSC_0357", "DSC_0358", "DSC_0359", "DSC_0360", "DSC_0362", "DSC_0363", "DSC_0364",
           "DSC_0365", "DSC_0366", "DSC_0394", "DSC_0395", "DSC_0419", "DSC_0420", "DSC_0421", "DSC_0422"]
GID = "meterwall16"
HARNESS = ROOT / "harness"
GT = ROOT / "harness-gt" / "facade-gt" / "scene.json"
OUT = ROOT / "harness-out"
POINTS_PER_VIEW = 60000
POSTHOC = ("exact", "lidar_modern.draw0", "nonlidar_modern.draw0", "nonlidar_2018.draw0")


def export(run: Path, dest: Path, sim=None) -> None:
    """PLY of every finite depth (random subset per view) and a cameras JSON, optionally moved by
    a similarity (s, R, t)."""
    rng = np.random.default_rng(0)
    pts, cams = [], []
    for m in MEMBERS:
        z = np.load(run / f"{m}.npz")
        d = np.where(z["valid"], z["depth"], np.nan)
        fx, fy, cx, cy = map(float, z["intrinsics"])
        T = z["cam_to_world"].astype(np.float64)
        if sim is not None:
            s, R, t = sim
            T = T.copy()
            T[:3, 3] = s * R @ T[:3, 3] + t
            T[:3, :3] = R @ T[:3, :3]
        h, w = d.shape
        v, u = np.nonzero(np.isfinite(d) & (d > 0))
        sel = rng.choice(len(u), min(POINTS_PER_VIEW, len(u)), replace=False)
        u, v = u[sel], v[sel]
        zz = d[v, u] * (sim[0] if sim is not None else 1.0)
        pc = np.c_[(u - cx) / fx * zz, (v - cy) / fy * zz, zz]
        pts.append(pc @ T[:3, :3].T + T[:3, 3])
        Rcw = T[:3, :3].T
        cams.append({"image": m, "R_cw": Rcw.tolist(), "t_cw": (-Rcw @ T[:3, 3]).tolist(),
                     "intrinsics": [fx, fy, cx, cy], "w": int(w), "h": int(h)})
    P = np.concatenate(pts).astype(np.float32)
    dest.mkdir(parents=True, exist_ok=True)
    with open(dest / "cloud.ply", "wb") as f:
        f.write(f"ply\nformat binary_little_endian 1.0\nelement vertex {len(P)}\nproperty float x\n"
                f"property float y\nproperty float z\nend_header\n".encode())
        f.write(P.tobytes())
    (dest / "cameras.json").write_text(json.dumps(cams))


def pose_alignment(dest: Path, metric: bool) -> dict:
    """Cloud -> GT transform from the cameras alone: rotation by Procrustes on the camera rotations
    (well determined even when the centres are nearly collinear, as on these 16 facade frames),
    scale (shape variants only) and translation from the centres. The harness default uses the
    centres only, which leaves the roll about the camera line free."""
    gkf = {k["id"]: k for k in json.loads(GT.read_text())["keyframes"]}
    flip = np.diag([1.0, -1.0, -1.0])
    Rs, Rg, cs, cg = [], [], [], []
    for c in json.loads((dest / "cameras.json").read_text()):
        Rcw = np.array(c["R_cw"])
        Rs.append(Rcw.T @ flip)
        cs.append(-Rcw.T @ np.array(c["t_cw"]))
        G = np.asarray(gkf[c["image"]]["pose"], float).reshape(4, 4, order="F")
        Rg.append(G[:3, :3])
        cg.append(G[:3, 3])
    U, _, Vt = np.linalg.svd(sum(g @ s.T for g, s in zip(Rg, Rs)))
    R = U @ np.diag([1, 1, np.linalg.det(U @ Vt)]) @ Vt
    cs, cg = np.array(cs), np.array(cg)
    a, b = cs - cs.mean(0), cg - cg.mean(0)
    s_fit = float(np.sum(b * (a @ R.T)) / np.sum(a * a))
    s = 1.0 if metric else s_fit
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = s * R, cg.mean(0) - s * R @ cs.mean(0)
    rms = float(np.sqrt(np.mean(np.sum((cs @ (s * R).T + T[:3, 3] - cg) ** 2, 1))))
    rot_err = [float(np.degrees(np.arccos(np.clip((np.trace(g.T @ R @ r) - 1) / 2, -1, 1)))) for g, r in zip(Rg, Rs)]
    info = {"method": "camera rotations (Procrustes) + centres" + ("" if metric else " + similarity scale"),
            "scale_applied": s, "camera_similarity_scale": s_fit, "cloud_scale_error_pct": 100 * (1 / s_fit - 1),
            "camera_rms_m": rms, "rotation_residual_deg_median": float(np.median(rot_err))}
    (dest / "transform.json").write_text(json.dumps(T.tolist()))
    (dest / "alignment.json").write_text(json.dumps(info, indent=1))
    return info


def score(dest: Path, metric: bool, producer: str, orient: bool = False) -> dict:
    out = dest / ("score_orient" if orient else "score")
    cmd = ["python", "-m", "evalharness.cli", "score-cloud", "--gt", str(GT), "--cloud", str(dest / "cloud.ply"),
           "--cameras", str(dest / "cameras.json"), "--producer", producer, "--out", str(out)]
    if metric:
        cmd.append("--metric")
    if orient:
        pose_alignment(dest, metric)
        cmd += ["--transform", str(dest / "transform.json")]
    subprocess.run(cmd, cwd=HARNESS, check=True, stdout=subprocess.DEVNULL)
    return json.loads((out / "geomscore.json").read_text())


def score_method(method: str) -> None:
    run = PRED_ROOT / "facade" / method / GID
    if not (run / "run.json").exists():
        return
    images_only = method.endswith("_images")
    claims_metric = not images_only or method.startswith("ma")
    variants = [("shape", None, False)]
    if claims_metric:
        variants.append(("metric", None, True))
    if images_only:
        src = np.array([np.load(run / f"{m}.npz")["cam_to_world"][:3, 3] for m in MEMBERS])
        for st in POSTHOC:
            poses = load_poses("facade", st)
            dst = np.array([poses[m]["T"][:3, 3] for m in MEMBERS])
            variants.append((f"posthoc-{st}", umeyama_sim3(src, dst), True))
    for name, sim, metric in variants:
        dest = OUT / method / name
        if not (dest / "cloud.ply").exists():
            export(run, dest, sim)
        for orient in (True, False):
            if not (dest / ("score_orient" if orient else "score") / "geomscore.json").exists():
                score(dest, metric, f"A2-{method}-{name}" + ("-orient" if orient else ""), orient)
                print(f"scored {method} {name} orient={orient}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", choices=list(runmod.ADAPTERS))
    ap.add_argument("--modes", nargs="+", default=["images"])
    ap.add_argument("--score-only", action="store_true")
    args = ap.parse_args()
    if args.model and not args.score_only:
        runmod.group_id = lambda n, members: GID
        adapter = runmod.ADAPTERS[args.model]()
        adapter.load()
        for mode in args.modes:
            info = runmod.run_group(adapter, "facade", len(MEMBERS), MEMBERS, mode)
            print(f"ran {adapter.key} {mode}: {info['seconds_total']}s", flush=True)
    for d in sorted((PRED_ROOT / "facade").iterdir()):
        if (d / GID / "run.json").exists():
            score_method(d.name)


if __name__ == "__main__":
    main()
