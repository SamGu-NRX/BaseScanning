"""Walkaround house: image-only COLMAP (pycolmap 4.2, the dataset's relaxed registration settings),
then undistortion to PINHOLE so pi3_dense.py can run on the refined poses.

    python house_sfm.py FRAMES_DIR WORK_DIR
"""

import json
import sys
import time
from pathlib import Path

import pycolmap

frames, work = Path(sys.argv[1]), Path(sys.argv[2])
work.mkdir(parents=True, exist_ok=True)
db = work / "db.db"
t0 = time.time()
if not db.exists():
    eo = pycolmap.FeatureExtractionOptions()
    eo.sift.max_num_features = 4096
    eo.max_image_size = 1280
    ro = pycolmap.ImageReaderOptions()
    ro.camera_model = "SIMPLE_RADIAL"
    pycolmap.extract_features(db, frames, camera_mode=pycolmap.CameraMode.SINGLE, reader_options=ro,
                              extraction_options=eo, device=pycolmap.Device.cpu)
    po = pycolmap.SequentialPairingOptions()
    po.overlap = 20
    po.quadratic_overlap = True
    pycolmap.match_sequential(db, pairing_options=po, device=pycolmap.Device.cpu)
t1 = time.time()
opts = pycolmap.IncrementalPipelineOptions()
opts.min_num_matches = 12
m = opts.mapper
m.init_min_tri_angle = 4
m.abs_pose_min_num_inliers = 15
m.abs_pose_min_inlier_ratio = 0.15
m.init_min_num_inliers = 50
(work / "sparse").mkdir(exist_ok=True)
done = [d for d in (work / "sparse").iterdir() if (d / "images.bin").exists()]
if done:
    recs = {i: pycolmap.Reconstruction(str(d)) for i, d in enumerate(done)}
else:
    recs = pycolmap.incremental_mapping(db, frames, work / "sparse", options=opts)
best = max(recs.values(), key=lambda r: r.num_reg_images())
(work / "best").mkdir(exist_ok=True)
best.write(work / "best")
t2 = time.time()
pycolmap.undistort_images(work / "undist", work / "best", frames)
und = pycolmap.Reconstruction(str(work / "undist/sparse"))
und.write_text(str(work / "undist/sparse"))
n = len([f for f in frames.iterdir() if f.suffix.lower() in (".jpg", ".png")])
rep = {"frames": n, "models": len(recs), "registered": best.num_reg_images(), "rate": round(best.num_reg_images() / n, 3),
       "points": best.num_points3D(), "reproj_px": best.compute_mean_reprojection_error(),
       "t_features_s": round(t1 - t0), "t_mapper_s": round(t2 - t1)}
(work / "sfm.json").write_text(json.dumps(rep, indent=1))
print(json.dumps(rep))
