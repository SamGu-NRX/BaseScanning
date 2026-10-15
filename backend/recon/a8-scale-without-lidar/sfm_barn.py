"""Scale-free COLMAP SfM of the Tanks and Temples Barn frames (137 of 410), then an undistorted PINHOLE model.

    ~/a8/venv/bin/python sfm_barn.py   ->  ~/a8/work/barn/{undistorted/images, undistorted/sparse, report.json}
"""

import json
import shutil
import time
from pathlib import Path

import pycolmap

SRC = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/data/houses/tt-barn/frames")
D = Path.home() / "a8/work/barn"
THREADS = 4


def main() -> None:
    D.mkdir(parents=True, exist_ok=True)
    db = D / "database.db"
    db.unlink(missing_ok=True)
    names = sorted(p.name for p in SRC.glob("*.jpg"))
    t0 = time.time()
    ext = pycolmap.FeatureExtractionOptions()
    ext.num_threads = THREADS
    ext.sift.max_num_features = 8192
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "SIMPLE_RADIAL"
    pycolmap.extract_features(str(db), str(SRC), image_names=names, camera_mode=pycolmap.CameraMode.SINGLE,
                              reader_options=reader, extraction_options=ext, device=pycolmap.Device.cpu)
    t1 = time.time()
    mopt = pycolmap.FeatureMatchingOptions()
    mopt.num_threads = THREADS
    pycolmap.match_exhaustive(str(db), matching_options=mopt, device=pycolmap.Device.cpu)
    t2 = time.time()
    tmp = D / "sparse_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    o = pycolmap.IncrementalPipelineOptions()
    o.num_threads = THREADS
    maps = pycolmap.incremental_mapping(str(db), str(SRC), str(tmp), options=o)
    t3 = time.time()
    key, best = max(maps.items(), key=lambda kv: kv[1].num_reg_images())
    und = D / "undistorted"
    shutil.rmtree(und, ignore_errors=True)
    pycolmap.undistort_images(str(und), str(tmp / str(key)), str(SRC))
    rep = {"frames": len(names), "extract_s": t1 - t0, "match_s": t2 - t1, "mapper_s": t3 - t2,
           "models_registered": sorted([m.num_reg_images() for m in maps.values()], reverse=True),
           "points": best.num_points3D()}
    (D / "report.json").write_text(json.dumps(rep, indent=1))
    print(rep)


if __name__ == "__main__":
    main()
