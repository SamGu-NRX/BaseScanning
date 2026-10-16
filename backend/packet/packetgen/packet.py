"""Assemble synthetic capture packets (docs/capture-packet-spec.md) for one house.

Layout written under <out>/<house-id>/:
  keyframes/k00001.jpg             shared by every variant (same pixels, different metadata)
  keyframes/k00001.depth.f32 ...   LiDAR variant only
  features/<variant>/k00001.bin    per variant (feature points carry that variant's drift)
  streams/<variant>/*.csv          per variant
  packet.<variant>.json            one manifest per variant; `export` makes a standalone packet
  house.json                       what was generated and from what
  _truth/truth.json                clean poses and every noise draw (never give this to a path under test)
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import __version__
from .align import Alignment, align
from .conventions import column_major, pixel_ray_arkit
from .depth import lidar_observe, render_depth
from .devices import DEVICES, VARIANTS
from .exif import K_from_focal35, exif_datetime, focal35_from_K, synth_exposure
from .images import PreparedFrame, luminance, open_upright, prepare, save_jpeg, sharpness
from .noise import apply_drift, draw_modern_walk, load_profiles
from .sensors import (
    DEFAULT_ANCHOR,
    HEADING_COLUMNS,
    LOCATION_COLUMNS,
    MOTION_COLUMNS,
    Trajectory,
    enu_from_arkit,
    feature_points,
    location_rows,
    motion_and_heading,
    plane_anchors,
    synth_timestamps,
    tracking_events,
    tracking_states,
)
from .sources import Recon, View

FORMAT_VERSION = "0.1"
PR7_GATES = {
    "trackingStableSeconds": 1.0, "minimumGroundLookDown": 30.0, "minimumContactSeparation": 2.0,
    "maximumAngleFromWallNormal": 60.0, "wallValidationTolerance": 0.0508, "minimumCameraOffsetFromWall": 0.1,
    "minimumRayAngle": 15.0, "maximumRayGap": 0.0508, "keyframeSpacingMeters": 0.5, "keyframeSpacingDegrees": 15.0,
}
UNITS = {
    "length": "meters", "angle": "degrees",
    "time": "seconds of device uptime, the clock of ARFrame.timestamp and CMDeviceMotion.timestamp",
    "image": "pixels of the saved JPEG", "rotationRate": "radians per second",
    "acceleration": "g (9.80665 m/s^2)", "magneticField": "microtesla",
}
CONVENTIONS = {
    "world": "ARKit world frame with worldAlignment .gravity: right-handed, y up (away from gravity), origin at the device's position when the session (epoch) started, -z the camera's initial facing projected onto the horizontal",
    "camera": "ARKit camera frame: +x right and +y up in the unrotated landscape sensor image, the camera looks along -z",
    "device": "Core Motion device frame: +x right and +y toward the top with the phone upright in portrait, +z out of the screen",
    "pose": "camera-to-world 4x4 matrix, 16 numbers column by column (simd_float4x4 layout), meters",
    "intrinsics": "[fx, fy, cx, cy] in pixels of the saved, unrotated landscape image",
    "pixel": "[u, v] continuous image coordinates: (0, 0) is the top-left corner of the top-left pixel, v grows down",
    "ray": "origin is the camera position; direction is a unit vector; camera-space direction ((u - cx)/fx, -(v - cy)/fy, -1)",
    "geodetic": "WGS84 degrees; altitude above mean sea level, ellipsoidalAltitude above the WGS84 ellipsoid, meters",
    "heading": "degrees clockwise from north, 0 to 360",
}
FEATURE_DTYPE = np.dtype([("xyz", "<f4", (3,)), ("id", "<u8")])
MAX_FEATURES = {"lidar_modern": 300, "nonlidar_modern": 250, "nonlidar_2018": 150}


@dataclass
class Frame:
    id: str
    img: str
    w: int
    h: int
    K: tuple
    T_true: np.ndarray
    view: View
    prep: PreparedFrame
    lum: float
    sharp: float
    n_features: int


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FileHasher:
    def __init__(self, root: Path):
        self.root = root
        self.cache: dict[str, tuple[int, str, float]] = {}

    def entry(self, rel: str, role: str) -> dict:
        p = self.root / rel
        st = p.stat()
        c = self.cache.get(rel)
        if c is None or c[2] != st.st_mtime:
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            c = (st.st_size, h, st.st_mtime)
            self.cache[rel] = c
        return {"path": rel, "bytes": c[0], "sha256": c[1], "role": role}


def source_to_prepared_uv(uv: np.ndarray, view: View, prep: PreparedFrame) -> np.ndarray:
    uv = np.asarray(uv, float).reshape(-1, 2)
    if prep.rotated_from_portrait:
        uv = np.stack([uv[:, 1], view.width - uv[:, 0]], axis=1)
    x0, y0 = prep.crop_box[:2]
    return np.stack([(uv[:, 0] - x0) * prep.scale[0], (uv[:, 1] - y0) * prep.scale[1]], axis=1)


def write_csv(path: Path, columns: list[str], rows, start_uptime: float, start_wall: dt.datetime, iso_col: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(columns)
        for r in rows:
            r = list(r)
            if iso_col:
                r[1] = iso(start_wall + dt.timedelta(seconds=float(r[0]) - start_uptime))
            w.writerow([("" if x is None else (f"{x:.6f}" if isinstance(x, float) and abs(x) < 1e4 else (f"{x:.4f}" if isinstance(x, float) else x))) for x in r])


def compose(R: np.ndarray, c: np.ndarray) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = c
    return T


class HouseBuilder:
    def __init__(self, house_id: str, recon: Recon, out_root: Path, *, seed: int | None = None,
                 anchor: dict | None = None, scan_points_src: np.ndarray | None = None,
                 source_dataset: str = "", notes: str = "", start_time: dt.datetime | None = None,
                 taps: list[dict] | None = None):
        self.house_id = house_id
        self.recon = recon
        self.dir = out_root / house_id
        self.seed = seed if seed is not None else int(hashlib.sha256(house_id.encode()).hexdigest()[:8], 16)
        self.rng = np.random.default_rng(self.seed)
        self.source_dataset = source_dataset
        self.notes = notes
        self.profiles = load_profiles()
        self.hasher = FileHasher(self.dir)
        self.taps_spec = taps
        a = dict(DEFAULT_ANCHOR)
        if anchor:
            a.update(anchor)
        else:
            r = self.rng.uniform(0, 3000)
            th = self.rng.uniform(0, 2 * math.pi)
            a["latitude"] += r * math.cos(th) / 111320.0
            a["longitude"] += r * math.sin(th) / (111320.0 * math.cos(math.radians(a["latitude"])))
        self.anchor = a
        self.north_az = float(self.rng.uniform(0, 360))
        self.start_wall = start_time or dt.datetime(2026, 9, 26, 15, 0, tzinfo=dt.timezone.utc) + dt.timedelta(
            minutes=float(self.rng.uniform(0, 180)))
        self.scan_points_src = scan_points_src

    # ---------------------------------------------------------------- clean scene

    def prepare_frames(self) -> None:
        self.align: Alignment = align(self.recon, self.rng)
        self.frames: list[Frame] = []
        (self.dir / "keyframes").mkdir(parents=True, exist_ok=True)
        for i, v in enumerate(self.recon.views):
            im = open_upright(v.image_path)
            if im.size != (v.width, v.height):
                raise ValueError(f"{v.name}: image is {im.size}, model says {(v.width, v.height)}")
            im2, prep = prepare(im, v.K, v.T_cv)
            fid = f"k{i + 1:05d}"
            rel = f"keyframes/{fid}.jpg"
            save_jpeg(im2, self.dir / rel)
            self.frames.append(Frame(fid, rel, prep.size[0], prep.size[1], prep.K, self.align.pose(prep.T_cv), v, prep,
                                     luminance(im2), sharpness(im2), int(len(np.unique(v.point_ids)))))
        self.R_true = np.array([f.T_true[:3, :3] for f in self.frames])
        self.c_true = np.array([f.T_true[:3, 3] for f in self.frames])
        self.t0 = float(self.rng.uniform(20000, 200000))
        self.times = synth_timestamps(self.c_true, self.R_true, self.rng, self.t0)
        self.start_uptime = self.t0 - float(self.rng.uniform(3, 8))
        self.traj = Trajectory(self.times, self.c_true, self.R_true)
        self.points_ar = self.align.point(self.recon.points)
        self.lookup = self.recon.point_lookup()
        self.scan_ar = None if self.scan_points_src is None else self.align.point(self.scan_points_src).astype(np.float32)
        fmt = {}
        for f in self.frames:
            fmt[(f.w, f.h)] = fmt.get((f.w, f.h), 0) + 1
        self.video_res = list(max(fmt, key=fmt.get))

    # ---------------------------------------------------------------- variants

    def build(self, variants: list[str] | None = None) -> dict:
        self.prepare_frames()
        variants = variants or list(VARIANTS)
        truth_variants = {}
        for k, name in enumerate(variants):
            spec = VARIANTS[name]
            if spec["tier"] == "photo_only":
                truth_variants[name] = self.build_pose_free(name, k)
            else:
                truth_variants[name] = self.build_arkit(name, k)
        self.write_truth(truth_variants)
        return self.write_house_json(variants)

    def _session(self, name: str, spec: dict, dev: dict, depth_on: bool) -> dict:
        s = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"packetgen/{self.house_id}/{name}")).upper(),
            "startedAt": iso(self.start_wall),
            "startedAtUptime": round(self.start_uptime, 4),
            "endedAtUptime": round(float(self.times[-1]) + 2.0, 4),
            "timeZone": self.anchor["timeZone"],
            "appVersion": f"packetgen-synthetic {__version__}",
            "deviceModel": dev["deviceModel"],
            "systemVersion": dev["systemVersion"],
            "tier": spec["tier"],
            "lidarAvailable": dev["lidarAvailable"] if spec["tier"] != "photo_only" else False,
            "sceneDepthEnabled": depth_on,
        }
        if spec["tier"] != "photo_only":
            s.update({
                "meshReconstructionSupported": dev["meshReconstructionSupported"],
                "sceneReconstruction": "none",
                "worldAlignment": "gravity",
                "planeDetection": ["horizontal", "vertical"],
                "videoFormat": {"imageResolution": self.video_res, "framesPerSecond": 60},
                "locationAuthorization": "fullAccuracy",
                "motionReferenceFrame": "xTrueNorthZVertical",
                "gates": PR7_GATES,
            })
        return s

    def _source(self, name: str, spec: dict, synthesized: list[str], notes: str) -> dict:
        return {
            "kind": "synthetic",
            "generator": f"packetgen {__version__}",
            "variant": name,
            "noiseProfile": spec["profile"] if spec["tier"] != "photo_only" else "none",
            "sourceDataset": self.source_dataset,
            "synthesizedFields": synthesized,
            "notes": notes,
        }

    def _exif(self, f: Frame, dev: dict, t_uptime: float, rng) -> tuple[dict, dict]:
        e = synth_exposure(f.lum, rng, dev["fNumber"])
        wall = self.start_wall + dt.timedelta(seconds=t_uptime - self.start_uptime)
        exif = {
            "Make": "Apple", "Model": dev["marketingName"], "LensModel": dev["lens"],
            "FocalLength": dev["focalLengthMm"],
            "FocalLenIn35mmFilm": int(round(focal35_from_K(f.K[0], f.w, f.h))),
            "PixelXDimension": f.w, "PixelYDimension": f.h, **e, **exif_datetime(wall),
        }
        exposure = {"duration": e["ExposureTime"], "offset": round(float(rng.normal(0, 0.15)), 3),
                    "iso": e["ISOSpeedRatings"][0], "fNumber": e["FNumber"]}
        return exif, exposure

    def build_arkit(self, name: str, vidx: int) -> dict:
        spec = VARIANTS[name]
        prof = self.profiles[spec["profile"]]
        dev = DEVICES[spec["profile"]]
        rng = np.random.default_rng([self.seed, vidx + 1])
        n = len(self.frames)
        n_feat = np.array([f.n_features for f in self.frames])

        prelim = n_feat < prof.insufficient_features_min
        walk_seed_offset = None
        if prof.name in ("lidar_modern", "nonlidar_modern"):
            # Filename order is not a walk. Integrate drift along the nearest-neighbour tour
            # and keep a draw inside the real-phone rigid-RMS band (diagnosis §2.2).
            R_obs, c_obs, epoch_idx, draw, rng, walk_seed_offset = draw_modern_walk(
                self.R_true, self.c_true, prof, self.seed, vidx, limited=prelim)
        else:
            R_obs, c_obs, epoch_idx, draw = apply_drift(self.R_true, self.c_true, prof, rng, limited=prelim)
        T_obs = [compose(R_obs[i], c_obs[i]) for i in range(n)]
        states = tracking_states(self.times, self.R_true, epoch_idx, draw.reset_index, n_feat, prof, rng)
        eid = [f"e{e + 1}" for e in epoch_idx]
        epochs = [{"id": "e1", "startTime": round(self.start_uptime, 4), "reason": "sessionStart"}]
        if draw.reset_index is not None:
            epochs.append({"id": "e2", "startTime": round(float(self.times[draw.reset_index]) - 0.3, 4),
                           "reason": "relocalizationFailed" if rng.random() < 0.5 else "trackingReset"})

        f_mult = 1.0 + rng.normal(0, prof.focal_sd_rel)
        pp = rng.normal(0, prof.principal_sd_px, 2)
        depth_on = prof.lidar and self.scan_ar is not None
        feat_dir = f"features/{name}"
        (self.dir / feat_dir).mkdir(parents=True, exist_ok=True)

        keyframes, files, frame_truth = [], [], []
        for i, f in enumerate(self.frames):
            s = f.w / 1920.0
            K_obs = (f.K[0] * f_mult, f.K[1] * f_mult, f.K[2] + pp[0] * s, f.K[3] + pp[1] * s)
            kf = {
                "id": f.id, "img": f.img, "w": f.w, "h": f.h,
                "intrinsics": [round(x, 3) for x in K_obs],
                "pose": [round(x, 6) for x in column_major(T_obs[i])],
                "timestamp": round(float(self.times[i]), 4),
                "tracking": states[i], "epoch": eid[i], "reason": "motion",
            }
            exif, exposure = self._exif(f, dev, float(self.times[i]), rng)
            kf["exposure"] = exposure
            if dev["arkitExifData"]:
                kf["exif"] = exif
            kf["lightEstimate"] = {"ambientIntensity": round(float(np.clip(1000 * f.lum / 0.45, 200, 2500)), 1),
                                   "ambientColorTemperature": round(float(rng.uniform(5200, 6800)), 0)}
            xyz, ids = feature_points(f.view, self.recon, self.lookup, self.align, f.T_true, T_obs[i],
                                      draw.scales[epoch_idx[i]], MAX_FEATURES[spec["profile"]], rng)
            rec = np.zeros(len(ids), FEATURE_DTYPE)
            rec["xyz"], rec["id"] = xyz, ids
            frel = f"{feat_dir}/{f.id}.bin"
            rec.tofile(self.dir / frel)
            kf["features"] = {"file": frel, "count": int(len(ids))}
            files.append(self.hasher.entry(frel, "features"))
            if depth_on:
                d_true, filled = render_depth(self.scan_ar, f.T_true, f.K, (f.w, f.h))
                d_obs, conf = lidar_observe(d_true, filled, rng)
                drel, crel = f"keyframes/{f.id}.depth.f32", f"keyframes/{f.id}.confidence.u8"
                d_obs.astype("<f4").tofile(self.dir / drel)
                conf.tofile(self.dir / crel)
                kf["depth"] = {"file": drel, "confidenceFile": crel, "w": int(d_obs.shape[1]), "h": int(d_obs.shape[0]), "smoothed": False}
                files += [self.hasher.entry(drel, "depth"), self.hasher.entry(crel, "confidence")]
            else:
                kf["depth"] = None
            keyframes.append(kf)
            files.append(self.hasher.entry(f.img, "keyframe"))
            err = T_obs[i][:3, 3] - f.T_true[:3, 3]
            frame_truth.append({
                "id": f.id, "source_image": f.view.name, "pose_true": column_major(f.T_true),
                "intrinsics_true": list(f.K), "epoch": eid[i],
                "position_error_m_vs_clean_world": float(np.linalg.norm(err)) if epoch_idx[i] == 0 else None,
                "yaw_error_deg": float(draw.yaw_error_deg[i]),
            })

        taps = self._taps(name, prof, T_obs, epoch_idx, draw.scales, f_mult, pp, eid, depth_on, rng)
        pts_for_planes = self.scan_ar if self.scan_ar is not None else self.points_ar
        planes = plane_anchors(pts_for_planes, self.c_true, [f.T_true for f in self.frames], T_obs, epoch_idx,
                               draw.scales, prof, dev["deviceModel"] != "iPhone8,1", rng)
        for p in planes:
            p["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.house_id}/{name}/{p['id']}")).upper()
            p["transform"] = [round(x, 6) for x in column_major(p["transform"])]
            p["planeExtent"] = {k: round(v, 4) for k, v in p["planeExtent"].items()}
            p["boundary"] = [[round(a, 4), round(b, 4)] for a, b in p["boundary"]]

        motion, heading, gravity_at, mh_truth = motion_and_heading(self.traj, self.anchor, self.north_az, rng)
        loc = location_rows(self.traj, self.anchor, self.north_az, rng)
        sdir = f"streams/{name}"
        write_csv(self.dir / sdir / "motion.csv", MOTION_COLUMNS, motion, self.start_uptime, self.start_wall, False)
        write_csv(self.dir / sdir / "heading.csv", HEADING_COLUMNS, heading, self.start_uptime, self.start_wall, True)
        write_csv(self.dir / sdir / "location.csv", LOCATION_COLUMNS, loc, self.start_uptime, self.start_wall, True)
        for fn in ("motion.csv", "heading.csv", "location.csv"):
            files.append(self.hasher.entry(f"{sdir}/{fn}", "stream"))

        synthesized = [
            "keyframes[].pose (clean pose from the source + ARKit-like drift, profile " + prof.name + ")",
            "keyframes[].intrinsics (source calibration + per-session focal and principal-point error)",
            "keyframes[].timestamp (walking-speed model)", "keyframes[].tracking", "epochs",
            "keyframes[].exposure", "keyframes[].lightEstimate", "keyframes[].features (SfM points re-expressed in the drifted world)",
            "planes (RANSAC on reference geometry)", "taps (auto reference point)", "streams.motion", "streams.heading", "streams.location",
        ]
        if dev["arkitExifData"]:
            synthesized.append("keyframes[].exif")
        if depth_on:
            synthesized.append("keyframes[].depth (laser scan rendered from the clean pose + LiDAR noise model)")
        notes = self.notes + (" LiDAR profile without a dense depth source: depth omitted." if prof.lidar and not depth_on else "")
        packet = {
            "format": "house-capture-packet", "formatVersion": FORMAT_VERSION,
            "packetId": str(uuid.uuid4()), "createdAt": iso(dt.datetime.now(dt.timezone.utc)),
            "units": UNITS, "conventions": CONVENTIONS,
            "source": self._source(name, spec, synthesized, notes.strip()),
            "session": self._session(name, spec, dev, depth_on),
            "epochs": epochs, "keyframes": keyframes, "stills": [],
            "taps": taps,
            "tracking": tracking_events(self.times, states, eid),
            "planes": planes,
            "streams": {
                "motion": {"file": f"{sdir}/motion.csv", "rateHz": 100, "referenceFrame": "xTrueNorthZVertical", "columns": MOTION_COLUMNS},
                "heading": {"file": f"{sdir}/heading.csv", "headingOrientation": "portrait", "headingFilter": 1.0, "columns": HEADING_COLUMNS},
                "location": {"file": f"{sdir}/location.csv", "desiredAccuracy": "best", "columns": LOCATION_COLUMNS},
            },
            "files": sorted({f["path"]: f for f in files}.values(), key=lambda x: x["path"]),
        }
        (self.dir / f"packet.{name}.json").write_text(json.dumps(packet, indent=1) + "\n")
        return {
            "profile": prof.name, "device": dev["deviceModel"], "scales": draw.scales,
            "session_multiplier": draw.session_multiplier, "reset_index": draw.reset_index,
            "walk_seed_offset": walk_seed_offset,
            "reset_heading_deg": draw.reset_heading_deg, "focal_multiplier": float(f_mult),
            "principal_point_offset_px_at_1920": [float(x) for x in pp], "depth": depth_on,
            "heading_error_deg": {"mean": float(np.mean(mh_truth["heading_error_deg_at"])), "sd": float(np.std(mh_truth["heading_error_deg_at"]))},
            "frames": frame_truth,
        }

    def _taps(self, name, prof, T_obs, epoch_idx, scales, f_mult, pp, eid, depth_on, rng) -> list[dict]:
        """Taps from the house annotations, else one auto `reference_point` on the reference
        geometry near the middle keyframe's image centre (never a made-up meter)."""
        specs = self.taps_spec
        if not specs:
            i = len(self.frames) // 2
            f = self.frames[i]
            if len(f.view.point_ids) == 0:
                return []
            uv = source_to_prepared_uv(f.view.point_uv, f.view, f.prep)
            ok = (uv[:, 0] > 0) & (uv[:, 0] < f.w) & (uv[:, 1] > 0) & (uv[:, 1] < f.h)
            if not ok.any():
                return []
            j = np.flatnonzero(ok)[np.argmin(np.linalg.norm(uv[ok] - [f.w / 2, f.h / 2], axis=1))]
            specs = [{"frame": i, "pixel": uv[j].tolist(), "label": "reference_point", "point_id": int(f.view.point_ids[j])}]
        out = []
        for k, sp in enumerate(specs):
            i = sp["frame"]
            f = self.frames[i]
            if "point_id" in sp:
                X = self.align.point(self.recon.points[self.lookup[sp["point_id"]]])
            else:
                X = np.asarray(sp["world_point"], float)
            s = f.w / 1920.0
            K_obs = (f.K[0] * f_mult, f.K[1] * f_mult, f.K[2] + pp[0] * s, f.K[3] + pp[1] * s)
            o, d = pixel_ray_arkit(T_obs[i], K_obs, np.asarray(sp["pixel"], float))
            rng_true = float(np.linalg.norm(X - f.T_true[:3, 3]))
            dist = scales[epoch_idx[i]] * rng_true
            out.append({
                "id": f"t{k + 1}", "time": round(float(self.times[i]) + 0.05, 4), "label": sp["label"],
                "tool": "mark", "step": "single", "keyframe": f.id, "frozen": False,
                "pixel": [round(x, 3) for x in sp["pixel"]],
                "rayOrigin": [round(x, 6) for x in o], "rayDirection": [round(x, 8) for x in d],
                "hit": {"position": [round(x, 6) for x in (o + d * dist)],
                        "target": "sceneDepth" if depth_on else "estimatedPlane", "alignment": "any",
                        "distance": round(dist, 4)},
                "confirmedByUser": True, "epoch": eid[i],
            })
        return out

    def build_pose_free(self, name: str, vidx: int) -> dict:
        """Photo-only tier: the same pixels with only what a camera app would record. Nothing
        derived from the reconstruction: no pose, no SfM intrinsics (EXIF 35 mm focal only), no
        features, planes, depth, motion or heading. Location is a per-photo coarse fix."""
        spec = VARIANTS[name]
        dev = DEVICES[spec["profile"]]
        rng = np.random.default_rng([self.seed, vidx + 1])
        stills, files = [], []
        for i, f in enumerate(self.frames):
            exif, _ = self._exif(f, dev, float(self.times[i]), rng)
            orientation = 6 if f.prep.rotated_from_portrait else 1
            exif["Orientation"] = orientation
            K = K_from_focal35(exif["FocalLenIn35mmFilm"], f.w, f.h)
            p = self.traj.pos(np.array([self.times[i]]))[0]
            e, nn, _ = enu_from_arkit(p, self.north_az)
            acc = float(rng.uniform(8, 30))
            e += rng.normal(0, acc / 1.5)
            nn += rng.normal(0, acc / 1.5)
            stills.append({
                "id": f"s{i + 1:05d}", "purpose": "house_overview", "img": f.img, "w": f.w, "h": f.h,
                "timestamp": round(float(self.times[i]), 4), "orientation": orientation,
                "intrinsics": [round(x, 3) for x in K], "intrinsicsSource": "exif_focal35",
                "location": {"latitude": round(self.anchor["latitude"] + nn / 111320.0, 7),
                             "longitude": round(self.anchor["longitude"] + e / (111320.0 * math.cos(math.radians(self.anchor["latitude"]))), 7),
                             "horizontalAccuracy": round(acc, 2)},
                "exif": exif,
            })
            files.append(self.hasher.entry(f.img, "still"))
        packet = {
            "format": "house-capture-packet", "formatVersion": FORMAT_VERSION,
            "packetId": str(uuid.uuid4()), "createdAt": iso(dt.datetime.now(dt.timezone.utc)),
            "units": {k: UNITS[k] for k in ("length", "angle", "time", "image")},
            "conventions": {k: CONVENTIONS[k] for k in ("world", "camera", "pose", "intrinsics", "pixel")},
            "source": self._source(name, spec, ["stills[].exif", "stills[].timestamp", "stills[].location",
                                                "stills[].intrinsics (from the rounded 35 mm focal length)"],
                                   (self.notes + " Pose-free control: the poses behind the other variants came from these same images.").strip()),
            "session": self._session(name, spec, dev, False),
            "keyframes": [], "stills": stills,
            "files": sorted({f["path"]: f for f in files}.values(), key=lambda x: x["path"]),
        }
        (self.dir / f"packet.{name}.json").write_text(json.dumps(packet, indent=1) + "\n")
        return {"device": dev["deviceModel"], "frames": len(stills)}

    # ---------------------------------------------------------------- records

    def write_truth(self, variants: dict) -> None:
        a = self.align
        truth = {
            "house_id": self.house_id,
            "warning": "Ground truth for scoring. Never feed this to a path under test.",
            "generator": f"packetgen {__version__}", "seed": self.seed,
            "source": {"dataset": self.source_dataset, "metric": self.recon.metric, "recon_source": self.recon.source,
                       "views": len(self.recon.views), "dropped_views": self.recon.dropped, "points": int(len(self.recon.points))},
            "alignment": {
                "up_src": a.up_src.tolist(), "up_vs_camera_mean_deg": a.up_vs_cameras_deg, "up_vs_hint_deg": a.up_vs_hint_deg,
                "scale": a.scale, "scale_source": a.scale_source, "scale_rel_sd": a.scale_rel_sd,
                "camera_height_above_ground_m": a.camera_height_m, "origin_src": a.origin_src.tolist(),
                "R_align_rows_src": a.R_align.tolist(), "planes": a.planes,
                "clean_world": "ARKit convention: gravity-aligned (estimated), origin at the first keyframe, -z its horizontal facing",
            },
            "true_north_azimuth_of_minus_z_deg": self.north_az,
            "anchor": self.anchor,
            "start_wall": iso(self.start_wall), "start_uptime": self.start_uptime,
            "keyframe_times": self.times.tolist(),
            "images": [{"id": f.id, "source": str(f.view.image_path), "rotated_from_portrait": f.prep.rotated_from_portrait,
                        "crop_box": list(f.prep.crop_box), "scale": list(f.prep.scale), "arkit_format": f.prep.arkit_format}
                       for f in self.frames],
            "variants": variants,
        }
        (self.dir / "_truth").mkdir(exist_ok=True)
        (self.dir / "_truth" / "truth.json").write_text(json.dumps(truth, indent=1, default=float) + "\n")

    def write_house_json(self, variants: list[str]) -> dict:
        path_len = float(np.sum(np.linalg.norm(np.diff(self.c_true, axis=0), axis=1)))
        info = {
            "house_id": self.house_id, "generator": f"packetgen {__version__}", "source_dataset": self.source_dataset,
            "keyframes": len(self.frames), "dropped_views": self.recon.dropped,
            "variants": {v: f"packet.{v}.json" for v in variants},
            "metric_truth": self.recon.metric, "scale_source": self.align.scale_source,
            "scale_rel_sd": self.align.scale_rel_sd, "path_length_m": round(path_len, 2),
            "duration_s": round(float(self.times[-1] - self.times[0]), 1),
            "depth_source": "laser scan" if self.scan_ar is not None else None,
            "truth": "_truth/truth.json",
            "notes": self.notes,
        }
        (self.dir / "house.json").write_text(json.dumps(info, indent=1) + "\n")
        return info
