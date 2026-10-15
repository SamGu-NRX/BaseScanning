"""A10 on real walkaround houses (robustness and coverage only: no ground truth, no ARKit).

    python house.py <house id> <N> [<N> ...]    -> results/houses/<house>/<set>.json

Sets: guided = the frames house.json tags as showing the meter, corners, ground to 14 ft and overhangs
(the shot list), filled evenly over the whole walk; careless = N consecutive frames centred on the first
meter frame (a homeowner who stays by the meter).
COLMAP: images only (videos carry no EXIF focal), one shared SIMPLE_RADIAL camera, SIFT 1600 px / 8192,
exhaustive matching within the set, the dataset agent's relaxed mapper (init_min_tri_angle 4,
abs_pose_min_num_inliers 15, abs_pose_min_inlier_ratio 0.15, min_num_matches 12, init_min_num_inliers 50).
pi3 / da3: all N frames in one pass; trajectory agreement with COLMAP on the frames COLMAP registered
(Sim(3), RMS as % of the trajectory extent; rotation error), the only consistency check available.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path.home() / "a2/code"))
import pycolmap  # noqa: E402

from run import THREADS, umeyama  # noqa: E402

STORE = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2")
HOUSES = STORE / "internal/data/houses"
HERE = STORE / "internal/breadth/a10-sparse-photo"
WORK = Path.home() / "a10/houses"
TAGS = ("meter", "corners", "ground_14ft", "overhangs")


def sets(house: str, n: int) -> dict[str, list[str]]:
    rec = json.loads((HOUSES / house / "house.json").read_text())
    frames = sorted(p.name for p in (HOUSES / house / "frames").glob("*.jpg"))
    n = min(n, len(frames))
    tagged = {t: [f for f in (rec["visible"].get(t, {}).get("frames") or []) if f in frames] for t in TAGS}
    must = sorted({f for v in tagged.values() for f in v})
    rest = [f for f in frames if f not in must]
    fill = [rest[int(i)] for i in np.linspace(0, len(rest) - 1, max(n - len(must), 0))] if n > len(must) else []
    guided = sorted(set(must + fill))[:n]
    m0 = frames.index(tagged["meter"][0]) if tagged["meter"] else len(frames) // 2
    a = int(np.clip(m0 - n // 2, 0, len(frames) - n))
    return {"guided": guided, "careless": frames[a:a + n]}, tagged


def colmap(house: str, names: list[str], wd: Path) -> tuple[dict, dict]:
    shutil.rmtree(wd, ignore_errors=True)
    (wd / "sparse").mkdir(parents=True)
    db = wd / "database.db"
    img_dir = HOUSES / house / "frames"
    ext = pycolmap.FeatureExtractionOptions()
    ext.max_image_size = 1600
    ext.num_threads = THREADS
    ext.sift.max_num_features = 8192
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "SIMPLE_RADIAL"
    t0 = time.time()
    pycolmap.extract_features(str(db), str(img_dir), image_names=names, camera_mode=pycolmap.CameraMode.SINGLE,
                              reader_options=reader, extraction_options=ext, device=pycolmap.Device.cpu)
    mopt = pycolmap.FeatureMatchingOptions()
    mopt.num_threads = THREADS
    pycolmap.match_exhaustive(str(db), matching_options=mopt, device=pycolmap.Device.cpu)
    t1 = time.time()
    o = pycolmap.IncrementalPipelineOptions()
    o.num_threads = THREADS
    o.random_seed = 0
    o.min_num_matches = 12
    o.mapper.init_min_tri_angle = 4
    o.mapper.abs_pose_min_num_inliers = 15
    o.mapper.abs_pose_min_inlier_ratio = 0.15
    o.mapper.init_min_num_inliers = 50
    maps = pycolmap.incremental_mapping(str(db), str(img_dir), str(wd / "sparse"), options=o)
    rep = {"match_s": round(t1 - t0, 1), "mapper_s": round(time.time() - t1, 1), "models": len(maps)}
    poses = {}
    if maps:
        best = max(maps.values(), key=lambda r: r.num_reg_images())
        rep.update(registered=best.num_reg_images(), model_sizes=sorted((m.num_reg_images() for m in maps.values()), reverse=True),
                   points3D=best.num_points3D(), mean_reproj_px=best.compute_mean_reprojection_error())
        for im in best.images.values():
            if im.has_pose:
                T = np.eye(4)
                T[:3, :3] = im.cam_from_world().rotation.matrix().T
                T[:3, 3] = im.projection_center()
                poses[im.name] = T
    else:
        rep["registered"] = 0
    return rep, poses


def ff(method: str, house: str, names: list[str], ref: dict) -> dict:
    import cv2  # noqa: PLC0415
    from models.common import cover_crop_resample, to_network_image  # noqa: PLC0415

    import run as R  # noqa: PLC0415

    img0 = cv2.imread(str(HOUSES / house / "frames" / names[0]))
    h0, w0 = img0.shape[:2]
    net = (518, int(round(518 * h0 / w0 / 14)) * 14) if method == "pi3" else (504, int(round(504 * h0 / w0 / 14)) * 14)
    key = (method, net)
    if key not in R._ADAPTERS:
        R._ADAPTERS.clear()
        R._ADAPTERS[key] = R.ff_adapter(method, net)
    rgbs = []
    for n in names:
        img = cv2.imread(str(HOUSES / house / "frames" / n))[..., ::-1]
        h, w = img.shape[:2]
        r = cover_crop_resample(w, h, net[0], net[1])
        rgbs.append(to_network_image(np.ascontiguousarray(img), r))
    t0 = time.perf_counter()
    preds = R._ADAPTERS[key].infer(rgbs, None, None)
    rep = {"infer_s": round(time.perf_counter() - t0, 1), "net": net}
    common = [i for i, n in enumerate(names) if n in ref]
    if len(common) < 3:
        rep["agreement"] = None
        return rep
    src = np.array([preds[i]["c2w"][:3, 3] for i in common])
    dst = np.array([ref[names[i]][:3, 3] for i in common])
    s, Rm, t = umeyama(src, dst)
    err = np.linalg.norm(s * src @ Rm.T + t - dst, axis=1)
    extent = float(np.linalg.norm(dst.max(0) - dst.min(0)))
    rot = [np.degrees(np.arccos(np.clip((np.trace(ref[names[i]][:3, :3].T @ (Rm @ preds[i]["c2w"][:3, :3])) - 1) / 2, -1, 1)))
           for i in common]
    rep["agreement"] = {"frames": len(common), "centre_rms_pct_extent": 100 * float(np.sqrt(np.mean(err**2))) / extent,
                        "centre_p90_pct_extent": 100 * float(np.percentile(err, 90)) / extent,
                        "rot_err_median_deg": float(np.median(rot)), "rot_err_p90_deg": float(np.percentile(rot, 90)),
                        "consistent_frames": int(np.sum((err / extent < 0.05) & (np.array(rot) < 5)))}
    return rep


def main() -> None:
    house, Ns = sys.argv[1], [int(x) for x in sys.argv[2:]]
    for n in Ns:
        ss, tagged = sets(house, n)
        for kind, names in ss.items():
            tag = f"N{len(names)}_{kind}"
            res = HERE / "results/houses" / house / f"{tag}.json"
            if res.exists():
                continue
            rep = {"house": house, "set": tag, "n": len(names), "images": names}
            rep["colmap"], poses = colmap(house, names, WORK / house / tag)
            rep["tagged_registered"] = {t: [f in poses for f in v if f in names] for t, v in tagged.items()}
            for m in ("pi3",):
                try:
                    rep[m] = ff(m, house, names, poses)
                except Exception as e:  # noqa: BLE001
                    rep[m] = {"error": repr(e)[-400:]}
            res.parent.mkdir(parents=True, exist_ok=True)
            res.write_text(json.dumps(rep, indent=1, default=float))
            c = rep["colmap"]
            print(f"{time.strftime('%T')} {house} {tag}: colmap {c.get('registered')}/{len(names)} models {c.get('models')} "
                  f"tagged {rep['tagged_registered']} pi3 {rep['pi3'].get('agreement') or rep['pi3'].get('error')} "
                  , flush=True)


if __name__ == "__main__":
    main()
