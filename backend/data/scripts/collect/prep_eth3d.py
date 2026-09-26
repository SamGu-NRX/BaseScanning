#!/usr/bin/env python3
"""Downsample an extracted ETH3D high-res scene into the store and rescale its COLMAP intrinsics.

Usage: prep_eth3d.py SCENE_DIR OUT_HOUSE_DIR [max_long_edge]
SCENE_DIR contains images/dslr_images_undistorted/*.JPG and dslr_calibration_undistorted/*.txt.
"""
import os
import shutil
import sys

from PIL import Image

src, out = sys.argv[1], sys.argv[2]
max_edge = int(sys.argv[3]) if len(sys.argv) > 3 else 2048
img_dir = os.path.join(src, "images", "dslr_images_undistorted")
cal_dir = os.path.join(src, "dslr_calibration_undistorted")
os.makedirs(os.path.join(out, "frames"), exist_ok=True)
os.makedirs(os.path.join(out, "calibration"), exist_ok=True)

# Per-camera scale factor: ETH3D cameras differ slightly in size, so each keeps its own factor.
cams = {}
lines = []
for line in open(os.path.join(cal_dir, "cameras.txt")):
    if line.startswith("#") or not line.strip():
        lines.append(line)
        continue
    p = line.split()
    cid, model, w, h = p[0], p[1], int(p[2]), int(p[3])
    assert model == "PINHOLE", model
    s = max_edge / max(w, h)
    nw, nh = round(w * s), round(h * s)
    sx, sy = nw / w, nh / h
    fx, fy, cx, cy = map(float, p[4:8])
    cams[cid] = (nw, nh)
    lines.append(f"{cid} PINHOLE {nw} {nh} {fx*sx:.4f} {fy*sy:.4f} {cx*sx:.4f} {cy*sy:.4f}\n")
open(os.path.join(out, "calibration", "cameras.txt"), "w").writelines(lines)

# images.txt: keep poses, rewrite image names to the frames/ basename, drop 2D points (they refer to full-res pixels).
img_cam = {}
out_lines = []
it = iter(open(os.path.join(cal_dir, "images.txt")))
for line in it:
    if line.startswith("#"):
        out_lines.append(line)
        continue
    p = line.split()
    if len(p) < 10:
        continue
    name = os.path.basename(p[9])
    img_cam[name] = p[8]
    out_lines.append(" ".join(p[:9] + [os.path.splitext(name)[0] + ".jpg"]) + "\n")
    out_lines.append("\n")
    next(it, None)
open(os.path.join(out, "calibration", "images.txt"), "w").writelines(out_lines)

n = 0
for name in sorted(os.listdir(img_dir)):
    if not name.lower().endswith(".jpg"):
        continue
    im = Image.open(os.path.join(img_dir, name)).convert("RGB")
    size = cams[img_cam[name]] if name in img_cam else None
    if size is None:
        s = max_edge / max(im.size)
        size = (round(im.width * s), round(im.height * s))
    im.resize(size, Image.LANCZOS).save(os.path.join(out, "frames", os.path.splitext(name)[0] + ".jpg"), quality=90)
    n += 1
print(f"{n} frames -> {out}")
