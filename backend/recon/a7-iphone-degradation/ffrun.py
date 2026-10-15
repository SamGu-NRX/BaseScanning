"""A2 leading paths on degraded frames: pi3 (images only, scaled by a Sim(3) fit of its camera centres to
the exact ETH3D centres = A2's "pi3 + exact scale" row) and DA3-Large fed exact poses + the frame's
intrinsics (A2's "DA3-L fed exact poses" row). Both isolate image-quality effects from ARKit scale.

    python ffrun.py <model: pi3|da3> <scene> <cond> [<cond> ...]

16 views per scene facing the scored elements: facade = A2's meter-wall set; electro = the 16 views
nearest the meter / w2 / w1 that see them. Frames are area-resized to 1024 px wide (A2's evaluation
width) and cover-cropped to an aspect-matched network size (4:3 -> 518x392 / 504x378; 3:2 dslr_ref ->
A2's 518x336 / 504x336). Depth -> cloud (60k points per view, 2 cm voxels) -> shared scorer with
rotation-aware camera alignment (A2's method, rigid because the cloud is metric after scaling).
Summary: <store>/internal/breadth/a7-iphone-degradation/results/<scene>/<model>/<cond>.json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "a2/code"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import a2ff.run as runmod  # noqa: E402
import torch  # noqa: E402

torch.set_num_threads(int(os.environ.get("A7_THREADS", 4)))
from a2ff.common import umeyama_sim3, write_view  # noqa: E402
from models.common import cover_crop_resample, intrinsics_to_network, to_network_image  # noqa: E402

from a1run import qvec_to_R  # noqa: E402
from degrade import WORK, read_cameras  # noqa: E402

STORE = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2")
RESULTS = STORE / "internal/breadth/a7-iphone-degradation/results"
HARNESS = Path.home() / "a7/harness"
SCORES = Path.home() / "a7/scores"
MEMBERS = {
    "facade": ["DSC_0361", "DSC_0357", "DSC_0358", "DSC_0359", "DSC_0360", "DSC_0362", "DSC_0363", "DSC_0364",
               "DSC_0365", "DSC_0366", "DSC_0394", "DSC_0395", "DSC_0419", "DSC_0420", "DSC_0421", "DSC_0422"],
    "electro": ["DSC_9303", "DSC_9302", "DSC_9272", "DSC_9301", "DSC_9297", "DSC_9296", "DSC_9271", "DSC_9295",
                "DSC_9294", "DSC_9300", "DSC_9273", "DSC_9283", "DSC_9270", "DSC_9257", "DSC_9274", "DSC_9260"],
}
POINTS_PER_VIEW = 60000
VOXEL = 0.02


class Pi3_43(runmod.Pi3):
    key = "pi3_518x392"
    net = (518, 392)


def da3(net):
    a = runmod.DA3("depth-anything/DA3-LARGE-1.1", "da3L", "Apache-2.0 (DA3-LARGE-1.1)")
    a.net = net
    return a


def gt_c2w(scene: str) -> dict[str, np.ndarray]:
    _, imgs = read_cameras(scene)
    out = {}
    for n, im in imgs.items():
        R = qvec_to_R(im["q"])
        T = np.eye(4)
        T[:3, :3], T[:3, 3] = R.T, -R.T @ np.array(im["t"])
        out[Path(n).stem] = T
    return out


def load_inputs(scene: str, cond: str, net):
    frames = json.loads((WORK / scene / cond / "frames.json").read_text())["frames"]
    by_stem = {Path(n).stem: f for n, f in frames.items()}
    rgbs, rs, Ks = [], [], []
    for m in MEMBERS[scene]:
        f = by_stem[m]
        img = cv2.imread(str(WORK / scene / cond / "images" / f["file"]))[..., ::-1]
        W = 1024
        H = int(round(f["h"] * W / f["w"]))
        rgb = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
        r = cover_crop_resample(W, H, net[0], net[1])
        rgbs.append(to_network_image(np.ascontiguousarray(rgb), r))
        rs.append(r)
        s = W / f["w"]
        fx, fy, cx, cy = f["K"]
        Ks.append(np.array([fx * s, fy * H / f["h"], cx * s - 0.5, cy * H / f["h"] - 0.5]))  # OpenCV centres
    return rgbs, rs, Ks


def export(run: Path, dest: Path, members: list[str], sim=None) -> None:
    rng = np.random.default_rng(0)
    pts, cams = [], []
    for m in members:
        z = np.load(run / f"{m}.npz")
        d = np.where(z["valid"], z["depth"], np.nan)
        fx, fy, cx, cy = map(float, z["intrinsics"])
        T = z["cam_to_world"].astype(np.float64).copy()
        sc = 1.0
        if sim is not None:
            sc, R, t = sim
            T[:3, 3] = sc * R @ T[:3, 3] + t
            T[:3, :3] = R @ T[:3, :3]
        h, w = d.shape
        v, u = np.nonzero(np.isfinite(d) & (d > 0))
        sel = rng.choice(len(u), min(POINTS_PER_VIEW, len(u)), replace=False)
        u, v = u[sel], v[sel]
        zz = d[v, u] * sc
        pc = np.c_[(u - cx) / fx * zz, (v - cy) / fy * zz, zz]
        pts.append(pc @ T[:3, :3].T + T[:3, 3])
        Rcw = T[:3, :3].T
        cams.append({"image": m, "R_cw": Rcw.tolist(), "t_cw": (-Rcw @ T[:3, 3]).tolist(),
                     "intrinsics": [fx, fy, cx, cy], "w": int(w), "h": int(h)})
    P = np.concatenate(pts)
    _, keep = np.unique(np.floor(P / VOXEL).astype(np.int64), axis=0, return_index=True)
    P = P[np.sort(keep)].astype(np.float32)
    dest.mkdir(parents=True, exist_ok=True)
    with open(dest / "cloud.ply", "wb") as f:
        f.write(f"ply\nformat binary_little_endian 1.0\nelement vertex {len(P)}\nproperty float x\n"
                f"property float y\nproperty float z\nend_header\n".encode())
        f.write(P.tobytes())
    (dest / "cameras.json").write_text(json.dumps(cams))


def pose_alignment(dest: Path, gt: Path) -> dict:
    """A2's rotation-aware rigid alignment: rotation by Procrustes on camera rotations, translation from centres."""
    gkf = {k["id"]: k for k in json.loads(gt.read_text())["keyframes"]}
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
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, cg.mean(0) - R @ cs.mean(0)
    rms = float(np.sqrt(np.mean(np.sum((cs @ R.T + T[:3, 3] - cg) ** 2, 1))))
    (dest / "transform.json").write_text(json.dumps(T.tolist()))
    return {"camera_rms_m": rms}


def summarize(path: Path) -> dict:
    g = json.loads(path.read_text())
    kd = g["key_distances"]
    comp = g["completeness"]
    return {"kd": kd["overall"], "kd_observed": kd["on_truly_observed_elements"], "status": kd["status_counts"],
            "false_observed_claims": len(kd["false_observed_claims"]),
            "scale_error_pct": g["scale"].get("scale_error_pct"),
            "anchor_m": g["anchor"].get("icp_translation_norm_m"),
            "wall_face_10cm_icp": comp["wall_face"]["after_icp"]["within_10cm"],
            "meter_wall_10cm_icp": comp["meter_wall"]["after_icp"]["within_10cm"],
            "rows": {r["id"]: r.get("error") for r in kd["rows"] if r["status"] == "scored"}}


def run_one(adapter, model: str, scene: str, cond: str) -> dict:
    three_two = cond == "dslr_ref"
    net = {"pi3": (518, 336) if three_two else (518, 392), "da3": (504, 336) if three_two else (504, 378)}[model]
    adapter.net = net
    members = MEMBERS[scene]
    rgbs, rs, Ks = load_inputs(scene, cond, net)
    gt = gt_c2w(scene)
    K_net = T = None
    if model == "da3":
        T = [gt[m] for m in members]
        K_net = [intrinsics_to_network(K, r) for K, r in zip(Ks, rs)]
    t0 = time.perf_counter()
    preds = adapter.infer(rgbs, K_net, T)
    secs = time.perf_counter() - t0
    run = SCORES / scene / model / cond / "preds"
    for m, r, p in zip(members, rs, preds):
        write_view(run, m, p["depth"], p["K"], p["c2w"], r, p.get("conf"), p.get("mask"))
    rep = {"scene": scene, "condition": cond, "model": model, "net": net, "infer_s": round(secs, 1),
           "conf_median": float(np.median([np.median(p["conf"]) for p in preds if p.get("conf") is not None]))}
    src = np.array([np.load(run / f"{m}.npz")["cam_to_world"][:3, 3] for m in members])
    dst = np.array([gt[m][:3, 3] for m in members])
    sim = umeyama_sim3(src, dst)
    fit = sim[0] * src @ sim[1].T + sim[2]
    rot = []
    for m in members:
        Rp = sim[1] @ np.load(run / f"{m}.npz")["cam_to_world"][:3, :3]
        rot.append(np.degrees(np.arccos(np.clip((np.trace(gt[m][:3, :3].T @ Rp) - 1) / 2, -1, 1))))
    rep["pose_centre_rms_sim3_m"] = float(np.sqrt(np.mean(np.sum((fit - dst) ** 2, 1))))
    rep["pose_rot_err_median_deg"] = float(np.median(rot))
    gtp = Path.home() / f"a7/gt/{scene}-gt/scene.json"
    variants = [("cloud", "score", sim if model == "pi3" else None, False)]
    if model == "pi3":
        # pi3's depth maps placed at the exact cameras with one global scale: "pi3 geometry fused on a known
        # trajectory" (A2's recommended use), which separates depth quality from pi3's own pose error.
        variants.append(("cloud_gtpose", "score_gtpose", sim, True))
    for folder, key, s, at_gt in variants:
        dest = SCORES / scene / model / cond / folder
        if at_gt:
            export_at_poses(run, dest, members, sim[0], gt)
        else:
            export(run, dest, members, s)
        align = pose_alignment(dest, gtp)
        if folder == "cloud":
            rep["alignment"] = align
        if folder == "cloud" and model == "pi3" and os.environ.get("A7_SKIP_OWNPOSE_SCORE"):
            rep["score_skipped"] = "pi3 own-pose cloud: surface metric only (time budget)"
            continue
        cmd = [sys.executable, "-m", "evalharness.cli", "score-cloud", "--gt", str(gtp), "--cloud", str(dest / "cloud.ply"),
               "--cameras", str(dest / "cameras.json"), "--metric", "--transform", str(dest / "transform.json"),
               "--producer", f"A7-{model}-{folder}-{cond}", "--out", str(dest / "score")]
        t0 = time.time()
        r = subprocess.run(cmd, cwd=HARNESS, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if r.returncode:
            rep[key + "_error"] = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else f"exit {r.returncode}"
        else:
            rep[key] = summarize(dest / "score" / "geomscore.json") | {"score_s": round(time.time() - t0, 1)}
    return rep


def export_at_poses(run: Path, dest: Path, members: list[str], scale: float, gt: dict) -> None:
    tmp = dest / "_preds"
    tmp.mkdir(parents=True, exist_ok=True)
    for m in members:
        z = dict(np.load(run / f"{m}.npz"))
        z["depth"] = z["depth"] * scale
        z["cam_to_world"] = gt[m]
        np.savez(tmp / f"{m}.npz", **z)
    export(tmp, dest, members, None)
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()


def main() -> None:
    model, scene, conds = sys.argv[1], sys.argv[2], sys.argv[3:]
    adapter = Pi3_43() if model == "pi3" else da3((504, 378))
    adapter.load()
    for cond in conds:
        res = RESULTS / scene / model / f"{cond}.json"
        if res.exists() or not (WORK / scene / cond / "frames.json").exists():
            continue
        res.parent.mkdir(parents=True, exist_ok=True)
        rep = run_one(adapter, model, scene, cond)
        res.write_text(json.dumps(rep, indent=1, default=float))
        s = rep.get("score", {}).get("kd", {})
        print(f"{time.strftime('%T')} {model} {scene} {cond}: infer {rep['infer_s']}s kd n={s.get('n')} "
              f"med={s.get('median_in')} p90={s.get('p90_in')} pose={rep['pose_centre_rms_sim3_m']:.3f}m", flush=True)


if __name__ == "__main__":
    main()
