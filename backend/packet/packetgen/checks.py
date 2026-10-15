"""Physical consistency checks of a generated house against its truth sidecar.

    python -m packetgen.checks <house-dir> [--images-root DIR]

- reprojection: SfM points through the clean ARKit poses and prepared intrinsics land on their
  observed pixels (tests crop, rotation, resize, pose and axis conversion);
- drift: realized error with PR #12's metric (displacement over D ft of walking, turned by the
  heading offset at the window start), plus the realized scale;
- IMU: motion.csv gravity at keyframe times vs gravity implied by the clean pose;
- compass and GPS: error against the truth north azimuth and trajectory;
- feature points: reprojection with the reported (noisy) pose and intrinsics.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np

from .calibrate import window_errors
from .conventions import DEVICE_FROM_CAMERA, from_column_major, heading_deg_of_vector, project_arkit
from .images import PreparedFrame
from .packet import FEATURE_DTYPE, source_to_prepared_uv
from .sensors import enu_from_arkit
from .sources import from_colmap_model

FEET, INCH = 0.3048, 0.0254


def read_csv(path: Path) -> tuple[list[str], np.ndarray]:
    with open(path) as f:
        r = csv.reader(f)
        head = next(r)
        rows = [[float(x) if x not in ("",) and not x[:1].isalpha() and "T" not in x else np.nan for x in row] for row in r]
    return head, np.array(rows)


def heading_of(v: np.ndarray) -> np.ndarray:
    return np.arctan2(v[..., 0], v[..., 2])


def check_house(house: Path, recon=None) -> dict:
    truth = json.loads((house / "_truth" / "truth.json").read_text())
    out: dict = {"house": house.name}
    T_true = {}
    for name, v in truth["variants"].items():
        if "frames" in v:
            for fr in v["frames"]:
                T_true[fr["id"]] = from_column_major(fr["pose_true"])
            break
    ids = sorted(T_true)

    if recon is not None:
        by_name = {vw.name: vw for vw in recon.views}
        errs = []
        a = truth["alignment"]
        R_align, origin, scale = np.array(a["R_align_rows_src"]), np.array(a["origin_src"]), a["scale"]
        imgs = {im["id"]: im for im in truth["images"]}
        pk_any = json.loads(next(house.glob("packet.arkit-*.json")).read_text())
        for kf in pk_any["keyframes"]:
            src_name = Path(imgs[kf["id"]]["source"]).name
            vw = by_name[src_name]
            fr = next(fr for fr in next(v for v in truth["variants"].values() if "frames" in v)["frames"] if fr["id"] == kf["id"])
            K = tuple(fr["intrinsics_true"])
            im = imgs[kf["id"]]
            prep = PreparedFrame((kf["w"], kf["h"]), K, np.eye(4), im["rotated_from_portrait"], tuple(im["crop_box"]), tuple(im["scale"]), im["arkit_format"])
            uv_obs = source_to_prepared_uv(vw.point_uv, vw, prep)
            idx = np.searchsorted(recon.point_ids, vw.point_ids)
            X = scale * (recon.points[idx] - origin) @ R_align.T
            uv, z = project_arkit(T_true[kf["id"]], K, X)
            ok = z > 0
            errs.append(np.linalg.norm(uv[ok] - uv_obs[ok], axis=1))
        e = np.concatenate(errs)
        out["reprojection_px_clean"] = {"median": float(np.median(e)), "p90": float(np.percentile(e, 90)), "n": int(len(e))}

    c_true = np.array([T_true[i][:3, 3] for i in ids])
    fwd_true = -np.array([T_true[i][:3, 2] for i in ids])
    out["variants"] = {}
    for vname, vt in truth["variants"].items():
        m = house / f"packet.{vname}.json"
        pk = json.loads(m.read_text())
        if pk["session"]["tier"] == "photo_only":
            continue
        res: dict = {"scales": vt["scales"], "reset_index": vt["reset_index"]}
        kfs = pk["keyframes"]
        e0 = [k for k in kfs if k["epoch"] == "e1"]
        c_obs = np.array([from_column_major(k["pose"])[:3, 3] for k in e0])
        fwd_obs = -np.array([from_column_major(k["pose"])[:3, 2] for k in e0])
        ct = c_true[: len(e0)]
        delta = heading_of(fwd_true[: len(e0)]) - heading_of(fwd_obs)
        drift = {}
        for ft in (3, 10, 20, 30):
            w = window_errors(c_obs, ct, delta, ft * FEET) / INCH
            if len(w):
                drift[ft] = [round(float(np.median(w)), 1), round(float(np.percentile(w, 90)), 1), int(len(w))]
        res["drift_in_epoch1"] = drift
        d_obs = np.linalg.norm(np.diff(c_obs, axis=0), axis=1)
        d_true = np.linalg.norm(np.diff(ct, axis=0), axis=1)
        good = d_true > 0.3
        res["realized_step_scale_median"] = float(np.median(d_obs[good] / d_true[good])) if good.any() else None
        res["tracking_states"] = {s: sum(1 for k in kfs if k["tracking"] == s) for s in sorted({k["tracking"] for k in kfs})}

        head, mo = read_csv(house / pk["streams"]["motion"]["file"])
        t = mo[:, 0]
        g = mo[:, [head.index("gravX"), head.index("gravY"), head.index("gravZ")]]
        ang = []
        for k in kfs:
            i = int(np.argmin(np.abs(t - k["timestamp"])))
            R = T_true[k["id"]][:3, :3]
            g_pose = (R @ DEVICE_FROM_CAMERA.T).T @ np.array([0, -1.0, 0])
            ang.append(math.degrees(math.acos(np.clip(g[i] @ g_pose / np.linalg.norm(g[i]), -1, 1))))
        res["imu_gravity_vs_pose_deg"] = {"median": float(np.median(ang)), "max": float(np.max(ang))}

        hh, hd = read_csv(house / pk["streams"]["heading"]["file"])
        herr = []
        for row in hd:
            i = int(np.argmin(np.abs(np.array([k["timestamp"] for k in kfs]) - row[0])))
            if abs(kfs[i]["timestamp"] - row[0]) < 0.05:
                true_h = heading_deg_of_vector(fwd_true[i], truth["true_north_azimuth_of_minus_z_deg"])
                herr.append((row[hh.index("trueHeading")] - true_h + 180) % 360 - 180)
        res["compass_error_deg_at_keyframes"] = {"mean": float(np.mean(herr)), "sd": float(np.std(herr)), "n": len(herr)} if herr else None

        lh, lo = read_csv(house / pk["streams"]["location"]["file"])
        lat0, lon0 = truth["anchor"]["latitude"], truth["anchor"]["longitude"]
        gerr = []
        times = np.array(truth["keyframe_times"])
        for row in lo[3:]:
            i = int(np.argmin(np.abs(times - row[0])))
            if abs(times[i] - row[0]) < 0.6:
                e_true, n_true, _ = enu_from_arkit(c_true[i], truth["true_north_azimuth_of_minus_z_deg"])
                e = (row[lh.index("longitude")] - lon0) * 111320 * math.cos(math.radians(lat0))
                n = (row[lh.index("latitude")] - lat0) * 111320
                gerr.append(math.hypot(e - e_true, n - n_true))
        res["gps_horizontal_error_m"] = {"median": float(np.median(gerr)), "p90": float(np.percentile(gerr, 90)), "n": len(gerr)} if gerr else None

        ferr = []
        for k in kfs[:: max(1, len(kfs) // 10)]:
            if not k.get("features"):
                continue
            rec = np.fromfile(house / k["features"]["file"], dtype=FEATURE_DTYPE)
            if len(rec) == 0:
                continue
            uv, z = project_arkit(from_column_major(k["pose"]), tuple(k["intrinsics"]), rec["xyz"].astype(float))
            ok = z > 0
            inside = ok & (uv[:, 0] >= 0) & (uv[:, 0] < k["w"]) & (uv[:, 1] >= 0) & (uv[:, 1] < k["h"])
            ferr.append(float(inside.mean()))
        res["features_inside_frame_fraction"] = float(np.mean(ferr)) if ferr else None

        depth_stats = None
        dks = [k for k in kfs if k.get("depth")]
        if dks:
            conf_frac, med = [], []
            for k in dks[:: max(1, len(dks) // 10)]:
                d = np.fromfile(house / k["depth"]["file"], "<f4").reshape(k["depth"]["h"], k["depth"]["w"])
                c = np.fromfile(house / k["depth"]["confidenceFile"], "u1")
                conf_frac.append([float((c == j).mean()) for j in (0, 1, 2)])
                med.append(float(np.median(d)))
            depth_stats = {"frames": len(dks), "confidence_low_med_high": np.mean(conf_frac, axis=0).round(3).tolist(),
                           "median_depth_m": float(np.median(med))}
        res["depth"] = depth_stats
        res["planes"] = {a: sum(1 for p in pk.get("planes", []) if p["alignment"] == a) for a in ("horizontal", "vertical")}
        out["variants"][vname] = res
    (house / "_truth" / "checks.json").write_text(json.dumps(out, indent=1) + "\n")
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("house")
    p.add_argument("--colmap-model", help="model the house was built from, for the reprojection check")
    p.add_argument("--images-root", default="")
    a = p.parse_args()
    recon = from_colmap_model(Path(a.colmap_model), Path(a.images_root), metric=True, source="check") if a.colmap_model else None
    print(json.dumps(check_house(Path(a.house), recon), indent=1))


if __name__ == "__main__":
    main()
