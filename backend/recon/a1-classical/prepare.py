"""Prepare an ETH3D scene: 2048 px images, the ground-truth COLMAP model at that size, and the
merged laser scan (1 cm voxels) in the ground-truth frame.

    python prepare.py facade electro delivery_area
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d
from plyfile import PlyData

from common import ETH3D, STORE, WORK, read_text_model, write_text_model

TARGET_W = 2048


def load_scan(scene: str, voxel: float = 0.01) -> o3d.geometry.PointCloud:
    d = ETH3D / scene / "dslr_scan_eval"
    root = ET.parse(d / "scan_alignment.mlp").getroot()
    merged = []
    for mesh in root.iter("MLMesh"):
        M = np.array(mesh.find("MLMatrix44").text.split(), float).reshape(4, 4)
        ply = PlyData.read(str(d / mesh.get("filename")))
        v = ply["vertex"]
        P = np.stack([v["x"], v["y"], v["z"]], 1).astype(np.float64)
        merged.append(P @ M[:3, :3].T + M[:3, 3])
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.concatenate(merged)))
    return pc.voxel_down_sample(voxel)


def prepare(scene: str) -> None:
    out = WORK / scene
    img_out = out / "images"
    img_out.mkdir(parents=True, exist_ok=True)
    if scene == "facade":
        src_calib = STORE / "internal/data/houses/eth3d-facade/calibration"
        cams, images = read_text_model(src_calib)
        frames = STORE / "internal/data/houses/eth3d-facade/frames"
        for name in images:
            dst = img_out / name
            if not dst.exists():
                dst.symlink_to(frames / name)
    else:
        cams0, images = read_text_model(ETH3D / scene / "dslr_calibration_undistorted")
        src_dir = ETH3D / scene / "images/dslr_images_undistorted"
        cams = {}
        for cid, (model, w, h, p) in cams0.items():
            assert model == "PINHOLE"
            W = TARGET_W
            H = int(round(h * W / w))
            sx, sy = W / w, H / h
            cams[cid] = (model, W, H, np.array([p[0] * sx, p[1] * sy, p[2] * sx, p[3] * sy]))
        for name, im in images.items():
            dst = img_out / name
            if dst.exists():
                continue
            img = cv2.imread(str(src_dir / name))
            _, W, H, _ = cams[im["camera_id"]]
            cv2.imwrite(str(dst), cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 95])
    write_text_model(out / "gt_model", cams, images)
    scan_path = out / "gt_scan_1cm.ply"
    if not scan_path.exists():
        pc = load_scan(scene)
        o3d.io.write_point_cloud(str(scan_path), pc)
        print(scene, "scan points (1 cm voxels):", len(pc.points))
    print(scene, "images:", len(images), "cameras:", len(cams))


if __name__ == "__main__":
    for s in sys.argv[1:]:
        prepare(s)
