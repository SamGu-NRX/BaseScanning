"""Classical sparse reconstruction of an ETH3D scene with COLMAP (pycolmap, CPU only).

The input is what a capture packet would give: the photos plus per-image intrinsics. The
dataset's poses are NOT used here; SfM recovers its own poses in an arbitrary similarity frame.
Metric scale and gravity are attached later (scene_from_recon.py) from simulated ARKit poses.

    python eth3d/recon_colmap.py --data ~/data/eth3d/facade --out ~/data/eval-harness/facade-colmap

Writes <out>/images (resized JPEGs), <out>/database.db, <out>/sparse/0 (COLMAP text model) and
<out>/recon_report.json (timings, registration counts).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pycolmap
from PIL import Image


def read_eth3d_intrinsics(calib: Path) -> tuple[dict[int, tuple[int, int, np.ndarray]], dict[str, int]]:
    cams = {}
    for ln in (calib / "cameras.txt").read_text().splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        p = ln.split()
        assert p[1] == "PINHOLE", p
        cams[int(p[0])] = (int(p[2]), int(p[3]), np.array(p[4:8], float))
    image_cam = {}
    lines = [ln for ln in (calib / "images.txt").read_text().splitlines() if not ln.startswith("#")]
    for head in lines[0::2]:
        if not head.strip():
            continue
        p = head.split()
        image_cam[Path(p[9]).name] = int(p[8])
    return cams, image_cam


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True, help="extracted ETH3D scene folder")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--width", type=int, default=1600, help="resize images to this width")
    ap.add_argument("--max-features", type=int, default=8192)
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()

    t0 = time.time()
    report: dict = {"width": args.width, "max_features": args.max_features}
    cams, image_cam = read_eth3d_intrinsics(args.data / "dslr_calibration_undistorted")
    src = args.data / "images/dslr_images_undistorted"
    img_dir = args.out / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    names = sorted(p.name for p in src.glob("*.JPG"))
    scale_of: dict[str, tuple[float, float, int, int]] = {}
    for n in names:
        w0, h0, _ = cams[image_cam[n]]
        w = args.width
        h = round(h0 * w / w0)
        scale_of[n] = (w / w0, h / h0, w, h)
        dst = img_dir / n
        if not dst.exists():
            im = Image.open(src / n)
            im.draft("RGB", (w, h))
            im.convert("RGB").resize((w, h), Image.Resampling.LANCZOS).save(dst, quality=92)
    report["t_resize_s"] = round(time.time() - t0, 1)

    db_path = args.out / "database.db"
    if db_path.exists():
        db_path.unlink()
    t1 = time.time()
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "PINHOLE"
    ext = pycolmap.FeatureExtractionOptions()
    ext.num_threads = args.threads
    ext.max_image_size = args.width
    ext.sift.max_num_features = args.max_features
    pycolmap.extract_features(
        db_path, img_dir, camera_mode=pycolmap.CameraMode.PER_IMAGE, reader_options=reader,
        extraction_options=ext, device=pycolmap.Device.cpu,
    )
    report["t_extract_s"] = round(time.time() - t1, 1)

    # Replace COLMAP's guessed focal lengths with the known intrinsics (the packet carries them).
    db = pycolmap.Database.open(db_path)
    for img in db.read_all_images():
        sx, sy, w, h = scale_of[img.name]
        _, _, k = cams[image_cam[img.name]]
        cam = db.read_camera(img.camera_id)
        cam.width, cam.height = w, h
        cam.params = np.array([k[0] * sx, k[1] * sy, k[2] * sx, k[3] * sy])
        cam.has_prior_focal_length = True
        db.update_camera(cam)
    db.close()

    t2 = time.time()
    mo = pycolmap.FeatureMatchingOptions()
    mo.num_threads = args.threads
    pycolmap.match_exhaustive(db_path, matching_options=mo, device=pycolmap.Device.cpu)
    report["t_match_s"] = round(time.time() - t2, 1)

    t3 = time.time()
    opts = pycolmap.IncrementalPipelineOptions()
    opts.num_threads = args.threads
    opts.ba_refine_focal_length = False
    opts.ba_refine_principal_point = False
    opts.ba_refine_extra_params = False
    opts.random_seed = 7
    sparse = args.out / "sparse"
    sparse.mkdir(exist_ok=True)
    recs = pycolmap.incremental_mapping(db_path, img_dir, sparse, opts)
    report["t_map_s"] = round(time.time() - t3, 1)
    models = sorted(recs.items(), key=lambda kv: -kv[1].num_reg_images())
    report["models"] = [
        {"id": int(i), "registered": r.num_reg_images(), "points": r.num_points3D()} for i, r in models
    ]
    best_id, best = models[0]
    out0 = sparse / "best"
    out0.mkdir(exist_ok=True)
    best.write_text(str(out0))
    report["best_model"] = int(best_id)
    report["images_total"] = len(names)
    report["registered"] = sorted(im.name for im in best.images.values() if im.has_pose)
    report["t_total_s"] = round(time.time() - t0, 1)
    (args.out / "recon_report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "registered"}, indent=1))


if __name__ == "__main__":
    main()
