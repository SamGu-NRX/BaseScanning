"""Robustness on online walkaround videos (no ground truth): SfM with unknown intrinsics, then
OpenMVS dense, then coverage diagnostics.

    python online.py <house_id> [--res-level 0]

Frames come from internal/data/houses/<id>/frames (t<sec>.jpg, capture order). One shared
SIMPLE_RADIAL camera (single video), focal refined in BA. Exhaustive matching (<= ~150 frames at
video resolution is cheap enough) so the loop closes without a vocabulary tree.
Outputs under WORK/<id>/: sparse/incremental (text), mvs/, report.json and a top-down density
image plan.png (gravity from the cameras' mean up vector; units are SfM units, not metres).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import time

import cv2
import numpy as np
import open3d as o3d
import pycolmap

from common import OPENMVS_BIN, STORE, WORK, dump_json

THREADS = 4


def run(house: str, res_level: int) -> dict:
    src = STORE / "internal/data/houses" / house / "frames"
    d = WORK / house
    d.mkdir(parents=True, exist_ok=True)
    img = d / "images"
    if not img.exists():
        img.symlink_to(src)
    db = d / "database.db"
    if db.exists():
        db.unlink()
    names = sorted(p.name for p in src.glob("*.jpg"))
    rep = {"house": house, "frames": len(names)}
    t0 = time.time()
    ext = pycolmap.FeatureExtractionOptions()
    ext.num_threads = THREADS
    ext.sift.max_num_features = 8192
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "SIMPLE_RADIAL"
    pycolmap.extract_features(str(db), str(img), image_names=names, camera_mode=pycolmap.CameraMode.SINGLE,
                              reader_options=reader, extraction_options=ext, device=pycolmap.Device.cpu)
    t1 = time.time()
    mopt = pycolmap.FeatureMatchingOptions()
    mopt.num_threads = THREADS
    pycolmap.match_exhaustive(str(db), matching_options=mopt, device=pycolmap.Device.cpu)
    t2 = time.time()
    tmp = d / "sparse_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    o = pycolmap.IncrementalPipelineOptions()
    o.num_threads = THREADS
    maps = pycolmap.incremental_mapping(str(db), str(img), str(tmp), options=o)
    t3 = time.time()
    rep.update({"extract_s": t1 - t0, "match_s": t2 - t1, "mapper_s": t3 - t2, "models": len(maps),
                "models_registered": sorted([m.num_reg_images() for m in maps.values()], reverse=True)})
    if not maps:
        dump_json(d / "report.json", rep)
        return rep
    best = max(maps.values(), key=lambda r: r.num_reg_images())
    # OpenMVS wants undistorted pinhole images.
    und = d / "undistorted"
    shutil.rmtree(und, ignore_errors=True)
    pycolmap.undistort_images(str(und), str(tmp / str(next(k for k, v in maps.items() if v is best))), str(img))
    und_model = pycolmap.Reconstruction(str(und / "sparse"))
    sp = d / "sparse" / "incremental"
    shutil.rmtree(sp, ignore_errors=True)
    sp.mkdir(parents=True)
    und_model.write_text(str(sp))
    shutil.rmtree(tmp, ignore_errors=True)
    cam = next(iter(best.cameras.values()))
    rep.update({"registered": best.num_reg_images(), "points3D": best.num_points3D(),
                "mean_reproj_px": best.compute_mean_reprojection_error(), "camera": str(cam.params.tolist()),
                "registered_names": sorted(im.name for im in best.images.values())})
    ws = d / "mvs"
    shutil.rmtree(ws, ignore_errors=True)
    (ws / "sparse").mkdir(parents=True)
    for f in ("cameras.txt", "images.txt", "points3D.txt"):
        shutil.copy(sp / f, ws / "sparse" / f)
    (ws / "images").symlink_to((und / "images").resolve())
    log = open(ws / "openmvs.log", "w")
    t4 = time.time()
    subprocess.run([str(OPENMVS_BIN / "InterfaceCOLMAP"), "-i", str(ws), "-o", str(ws / "scene.mvs"),
                    "--image-folder", str(ws / "images"), "-w", str(ws)], check=True, stdout=log, stderr=subprocess.STDOUT)
    subprocess.run([str(OPENMVS_BIN / "DensifyPointCloud"), str(ws / "scene.mvs"), "-w", str(ws),
                    "--resolution-level", str(res_level), "--max-threads", str(THREADS)],
                   check=True, stdout=log, stderr=subprocess.STDOUT)
    t5 = time.time()
    for f in ws.glob("depth*.dmap"):
        f.unlink()
    rep["densify_s"] = t5 - t4
    rep["total_s"] = t5 - t0
    pc = o3d.io.read_point_cloud(str(ws / "scene_dense.ply"))
    rep["dense_points"] = len(pc.points)
    rep.update(plan_image(und_model, np.asarray(pc.points), np.asarray(pc.colors), d / "plan.png"))
    dump_json(d / "report.json", rep)
    return rep


def plan_image(rec: pycolmap.Reconstruction, P: np.ndarray, C: np.ndarray, out) -> dict:
    """Top-down density map in a gravity frame from the cameras' mean up vector (-y of OpenCV
    camera axes); camera centres drawn in red."""
    ups, cs = [], []
    for im in rec.images.values():
        cfw = im.cam_from_world() if callable(im.cam_from_world) else im.cam_from_world
        R = np.asarray(cfw.rotation.matrix())
        ups.append(-R[1])
        cs.append(-R.T @ np.asarray(cfw.translation))
    up = np.mean(ups, 0)
    up /= np.linalg.norm(up)
    cs = np.array(cs)
    a = np.cross(up, [1.0, 0, 0])
    if np.linalg.norm(a) < 0.1:
        a = np.cross(up, [0, 0, 1.0])
    a /= np.linalg.norm(a)
    b = np.cross(up, a)
    xy = np.c_[P @ a, P @ b]
    cxy = np.c_[cs @ a, cs @ b]
    lo = np.percentile(np.r_[xy, cxy], 1, axis=0)
    hi = np.percentile(np.r_[xy, cxy], 99, axis=0)
    span = (hi - lo).max() * 1.1
    lo = (lo + hi) / 2 - span / 2
    N = 900
    ij = ((xy - lo) / span * N).astype(int)
    k = (ij >= 0).all(1) & (ij < N).all(1)
    H = np.zeros((N, N))
    np.add.at(H, (ij[k, 1], ij[k, 0]), 1)
    im = (255 * np.clip(np.log1p(H) / np.log1p(np.percentile(H[H > 0], 99)), 0, 1)).astype(np.uint8)
    im = cv2.cvtColor(255 - im, cv2.COLOR_GRAY2BGR)
    for p in ((cxy - lo) / span * N).astype(int):
        cv2.circle(im, (int(p[0]), int(p[1])), 3, (0, 0, 255), -1)
    cv2.imwrite(str(out), im)
    return {"plan_png": str(out), "cam_path_span_sfm_units": float(np.ptp(cxy, 0).max())}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("house")
    ap.add_argument("--res-level", type=int, default=0)
    a = ap.parse_args()
    r = run(a.house, a.res_level)
    r.pop("registered_names", None)
    print(r)
