"""COLMAP model -> points_model.ply + cameras_model.json (the recon-dir layout e2e_scene.py reads).
With --add DIR, also append that recon dir's points (e.g. pi3 dense on the same poses).

    python export_model.py MODEL_DIR OUT_DIR [--add PI3_DIR]
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pycolmap
from plyfile import PlyData, PlyElement

ap = argparse.ArgumentParser()
ap.add_argument("model", type=Path)
ap.add_argument("out", type=Path)
ap.add_argument("--add", type=Path)
a = ap.parse_args()
rec = pycolmap.Reconstruction(str(a.model))
a.out.mkdir(parents=True, exist_ok=True)
X = np.array([p.xyz for p in rec.points3D.values()], np.float32)
n_sparse = len(X)
if a.add:
    d = PlyData.read(str(a.add / "points_model.ply"))["vertex"]
    X = np.concatenate([X, np.c_[d["x"], d["y"], d["z"]].astype(np.float32)])
el = np.zeros(len(X), dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")])
el["x"], el["y"], el["z"] = X.T
PlyData([PlyElement.describe(el, "vertex")], text=False, byte_order="<").write(str(a.out / "points_model.ply"))
cams = []
for im in sorted(rec.images.values(), key=lambda i: i.name):
    cfw = im.cam_from_world() if callable(im.cam_from_world) else im.cam_from_world
    cam = rec.cameras[im.camera_id]
    fx, fy, cx, cy = (cam.params[:4] if cam.model.name in ("PINHOLE", "OPENCV") else
                      [cam.params[0], cam.params[0], cam.params[1], cam.params[2]])
    cams.append({"image": Path(im.name).stem, "R_cw": cfw.rotation.matrix().tolist(), "t_cw": np.asarray(cfw.translation).tolist(),
                 "intrinsics": [float(fx), float(fy), float(cx), float(cy)], "w": int(cam.width), "h": int(cam.height)})
(a.out / "cameras_model.json").write_text(json.dumps(cams))
print(json.dumps({"sparse_points": n_sparse, "total_points": int(len(X)), "cameras": len(cams)}))
