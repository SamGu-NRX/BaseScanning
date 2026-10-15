"""Register every input photo to its Marble world (splat frame, metres via metric_scale_factor).

python register_inputs.py WORLD_DIR --calib CALIB_DIR          (ETH3D: per-image intrinsics)
python register_inputs.py WORLD_DIR --fx 680                    (video: shared pinhole, centred)
Writes WORLD_DIR/cameras.json (harness format), WORLD_DIR/points_metric.ply, WORLD_DIR/registration.json.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from splatpose import cams_json, match_lg, read_colmap_intrinsics, read_splat_ply, register, render, splat_points  # noqa: E402

sys.path.insert(0, str(Path(__file__).parents[2] / "eval-harness"))
from evalharness.sceneio import write_ply  # noqa: E402


def rot(yaw, pitch):
    """World-to-camera rotation for an OpenCV camera at the origin, yaw about -y (down axis), pitch about x."""
    cy, sy = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))
    cp, sp = np.cos(np.radians(pitch)), np.sin(np.radians(pitch))
    Ry = np.array([[cy, 0, -sy], [0, 1, 0], [sy, 0, cy]])
    Rx = np.array([[1, 0, 0], [0, cp, sp], [0, -sp, cp]])
    return Rx @ Ry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("world", type=Path)
    ap.add_argument("--calib", type=Path)
    ap.add_argument("--fx", type=float)
    ap.add_argument("--min-inliers", type=int, default=40)
    ap.add_argument("--ds", type=int, default=3)
    a = ap.parse_args()
    t0 = time.time()
    W = json.loads((a.world / "world.json").read_text())
    msf = W["assets"]["splats"]["semantics_metadata"]["metric_scale_factor"]
    arr = read_splat_ply(a.world / "splats_full_res.ply")
    xyz, rgb, op, sc = splat_points(arr, msf)
    write_ply(a.world / "points_metric.ply", xyz.astype(np.float32),
              {"opacity": op.astype(np.float32), "r": (rgb[:, 0] * 255).astype(np.uint8),
               "g": (rgb[:, 1] * 255).astype(np.uint8), "b": (rgb[:, 2] * 255).astype(np.uint8)})
    intr = read_colmap_intrinsics(a.calib) if a.calib else {}
    photos = sorted((a.world / "inputs").glob("*.jpg"))
    entries, report = [], {}
    prev = None
    for p in photos:
        img = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        h, w = img.shape[:2]
        K = intr[p.stem][0] if p.stem in intr else np.array([a.fx, a.fx, w / 2, h / 2])
        seeds = [(rot(y, pt), np.zeros(3)) for y in (0, 40, -40, 80, -80, 120, -120, 160) for pt in (15, -5)]
        seeds = ([prev] if prev is not None else []) + seeds
        best = None
        for R0, t0_ in seeds:
            R, t, st = register(img, K, xyz, rgb, R0, t0_, iters=1, ds=a.ds)
            if st.get("ok") and (best is None or st["inliers"] > best[2]["inliers"]):
                best = (R, t, st)
            if best is not None and best[2]["inliers"] >= 150:
                break
        if best is None:
            report[p.stem] = {"ok": False}
            print(p.stem, "FAILED", flush=True)
            continue
        R, t, st = register(img, K, xyz, rgb, best[0], best[1], iters=6, ds=a.ds)
        st["ok"] = bool(st.get("ok") and st["inliers"] >= a.min_inliers)
        report[p.stem] = {**st, "centre_m": (-R.T @ t).round(3).tolist(), "K": K.tolist()}
        print(p.stem, st, flush=True)
        if st["ok"]:
            entries.append((p.stem, R, t, K, w, h))
            prev = (R, t)
            ren, _ = render(xyz, rgb, R, t, K, w, h, ds=4)
            small = cv2.resize(img, (ren.shape[1], ren.shape[0]))
            cv2.imwrite(str(a.world / f"reg_{p.stem}.jpg"), cv2.cvtColor(np.concatenate([small, ren], 1), cv2.COLOR_RGB2BGR))
    # second pass: seed failed photos from registered neighbours (photo-photo matches lifted by the
    # neighbour's render depth -> PnP), then refine against the splat as usual
    for p in photos:
        if any(e[0] == p.stem for e in entries):
            continue
        img = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        h, w = img.shape[:2]
        K = intr[p.stem][0] if p.stem in intr else np.array([a.fx, a.fx, w / 2, h / 2])
        small = cv2.resize(img, (int(np.ceil(w / a.ds)), int(np.ceil(h / a.ds))), interpolation=cv2.INTER_AREA)
        best = None
        for stem_j, Rj, tj, Kj, wj, hj in list(entries):
            imj = cv2.cvtColor(cv2.imread(str(a.world / "inputs" / f"{stem_j}.jpg")), cv2.COLOR_BGR2RGB)
            smj = cv2.resize(imj, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA)
            _, dep = render(xyz, rgb, Rj, tj, Kj, wj, hj, a.ds)
            pa, pb = match_lg(small, smj, (dep > 0).astype(np.uint8))
            if len(pa) < 20:
                continue
            d = dep[np.clip(pb[:, 1].astype(int), 0, dep.shape[0] - 1), np.clip(pb[:, 0].astype(int), 0, dep.shape[1] - 1)]
            ok = d > 0
            uf, vf = (pb[ok, 0] + 0.5) * a.ds, (pb[ok, 1] + 0.5) * a.ds
            Xc = np.c_[(uf - Kj[2]) / Kj[0] * d[ok], (vf - Kj[3]) / Kj[1] * d[ok], d[ok]]
            Xw = (Xc - tj) @ Rj
            Km = np.array([[K[0], 0, K[2]], [0, K[1], K[3]], [0, 0, 1]])
            okp, rv, tv, inl = cv2.solvePnPRansac(Xw, (pa[ok] + 0.5) * a.ds, Km, None, reprojectionError=12.0, iterationsCount=3000)
            if okp and inl is not None and (best is None or len(inl) > best[2]):
                best = (cv2.Rodrigues(rv)[0], tv.ravel(), len(inl), stem_j)
        if best is None:
            continue
        R, t, st = register(img, K, xyz, rgb, best[0], best[1], iters=6, ds=a.ds)
        st["ok"] = bool(st.get("ok") and st["inliers"] >= a.min_inliers)
        st["seeded_from"] = best[3]
        report[p.stem] = {**st, "centre_m": (-R.T @ t).round(3).tolist(), "K": K.tolist()}
        print(p.stem, "pass2", st, flush=True)
        if st["ok"]:
            entries.append((p.stem, R, t, K, w, h))
            ren, _ = render(xyz, rgb, R, t, K, w, h, ds=4)
            sm4 = cv2.resize(img, (ren.shape[1], ren.shape[0]))
            cv2.imwrite(str(a.world / f"reg_{p.stem}.jpg"), cv2.cvtColor(np.concatenate([sm4, ren], 1), cv2.COLOR_RGB2BGR))
    (a.world / "cameras.json").write_text(json.dumps(cams_json(entries), indent=1))
    (a.world / "registration.json").write_text(json.dumps({"metric_scale_factor": msf, "points": int(len(xyz)),
                                                           "runtime_s": round(time.time() - t0, 1), "images": report}, indent=1))
    print("registered", len(entries), "/", len(photos), "in", round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
