"""Generate packets for every house in internal/data/manifest.json that has none yet (or whose
files changed). Tolerant of the manifest's shape, which another agent is still writing:
a list or {"houses": [...]}, with id / path / kind under several common names."""

from __future__ import annotations

import hashlib
import json
import re
import traceback
from pathlib import Path

import cv2
import numpy as np

from .depth import load_eth3d_scan
from .images import normalize_images
from .packet import HouseBuilder
from .photos import build_photo_only
from .sources import IMAGE_EXTS, from_colmap_model, list_images, run_sfm

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".MP4", ".MOV", ".webm", ".mkv"}
SEQUENCE_KINDS = {"sequence", "video", "walkaround", "walkaround_video", "frames", "frame_sequence", "orbit", "multiview", "multi_view"}
PHOTO_KINDS = {"photos", "photo", "single", "singles", "listing", "listing_photos", "stills", "unordered"}
STATIC_KINDS = {"static_video", "fixed_camera", "timelapse"}
MAX_FRAMES = 150
MAX_STATIC_STILLS = 6
ETH3D_ROOT = Path.home() / "work" / "data" / "eth3d"
VISIBLE_PURPOSE = {"meter": "meter_surround", "meter_can": "meter_surround", "panel": "panel_surround"}


def visible_frames(entry: dict) -> dict[str, list[str]]:
    vis = entry.get("visible") if isinstance(entry.get("visible"), dict) else {}
    return {tag: list(v.get("frames") or []) for tag, v in vis.items() if isinstance(v, dict)}


def still_purposes(entry: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag, frames in visible_frames(entry).items():
        for f in frames:
            if tag in VISIBLE_PURPOSE and f not in out:
                out[f] = VISIBLE_PURPOSE[tag]
    return out


def pick_static_stills(entry: dict, images: list[Path]) -> list[Path]:
    """A fixed camera has no baseline for SfM, and a phone would send a few stills of it, not every
    frame: keep the annotated frames, then fill with evenly spaced ones."""
    by_name = {p.name: p for p in images}
    picked = [by_name[f] for fs in visible_frames(entry).values() for f in fs if f in by_name]
    picked = list(dict.fromkeys(picked))[:MAX_STATIC_STILLS]
    rest = [p for p in images if p not in picked]
    need = MAX_STATIC_STILLS - len(picked)
    if need > 0 and rest:
        idx = sorted({int(round(i * (len(rest) - 1) / max(1, need - 1))) for i in range(need)})
        picked += [rest[i] for i in idx]
    return sorted(picked, key=lambda p: p.name)


def load_scan(hid: str, work_root: Path, log) -> np.ndarray | None:
    """ETH3D laser scan for LiDAR depth, if `scripts/fetch_gt.sh eth3d <scene>` or the README
    download put it under ~/work/data/eth3d/<scene>/dslr_scan_eval."""
    if not hid.startswith("eth3d-"):
        return None
    scan_dir = ETH3D_ROOT / hid.removeprefix("eth3d-") / "dslr_scan_eval"
    if not (scan_dir / "scan_alignment.mlp").exists():
        log(f"{hid}: no laser scan at {scan_dir}; LiDAR variant gets no depth maps")
        return None
    return load_eth3d_scan(scan_dir, 6_000_000, np.random.default_rng(0))


def source_label(entry: dict, default: str) -> str:
    src = entry.get("source")
    if isinstance(src, dict):
        parts = [src.get("name"), src.get("url")]
        lic = entry.get("licence") or entry.get("license")
        return "; ".join(str(p) for p in parts + [lic] if p)
    return str(first(entry, "source", "source_url", "url", "license", default=default))


def entries(manifest: dict | list) -> list[dict]:
    if isinstance(manifest, list):
        return manifest
    for key in ("houses", "items", "entries", "scenes", "data"):
        if isinstance(manifest.get(key), list):
            return manifest[key]
    if isinstance(manifest.get("houses"), dict):
        return [dict(v, id=k) for k, v in manifest["houses"].items()]
    return []


def first(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default


def house_dir(entry: dict, data_root: Path, hid: str) -> Path:
    p = first(entry, "path", "dir", "folder", "directory", "local_path", "images_dir", "frames_dir")
    if p:
        for base in (data_root, data_root / "houses", data_root.parent, data_root.parent.parent):
            q = (base / p) if not Path(p).is_absolute() else Path(p)
            if q.exists():
                return q
    return data_root / "houses" / hid


def fingerprint(files: list[Path]) -> str:
    h = hashlib.sha256()
    for f in sorted(files):
        h.update(f"{f.name}:{f.stat().st_size}".encode())
    return h.hexdigest()[:16]


def extract_video_frames(video: Path, out: Path, fps: float = 2.0) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    native = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(round(native / fps)))
    if total and total / step > MAX_FRAMES:
        step = int(total / MAX_FRAMES) + 1
    paths, i = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step == 0:
            p = out / f"{video.stem}_{i:06d}.jpg"
            cv2.imwrite(str(p), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            paths.append(p)
        i += 1
    cap.release()
    return paths


def looks_sequential(images: list[Path]) -> bool:
    nums = [re.findall(r"\d+", p.stem) for p in images]
    return len(images) >= 12 and all(n for n in nums)


def run_manifest(manifest_path: Path, out_root: Path, work_root: Path, only: list[str] | None = None, force: bool = False,
                 log=print) -> list[dict]:
    data_root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    results = []
    for entry in entries(manifest):
        hid = str(first(entry, "id", "house_id", "slug", "name", default="")).strip()
        if not hid or (only and hid not in only):
            continue
        folder = house_dir(entry, data_root, hid)
        hid = re.sub(r"[^a-zA-Z0-9_-]+", "-", hid).strip("-")
        if not folder.exists():
            results.append({"house_id": hid, "status": "missing_folder", "folder": str(folder)})
            continue
        images = [p for p in list_images(folder) if "/_" not in str(p)]
        videos = sorted(p for p in folder.rglob("*") if p.suffix in VIDEO_EXTS)
        fp = fingerprint(images + videos)
        done = out_root / hid / "house.json"
        if done.exists() and not force:
            prev = json.loads(done.read_text())
            if prev.get("source_fingerprint") == fp:
                results.append({"house_id": hid, "status": "up_to_date"})
                continue
        kind = str(first(entry, "source_type", "kind", "type", "capture_type", "capture", "media_type", "format", default="")).lower()
        if kind == "gt_dataset" and done.exists():
            # Built from the dataset's own ground truth by `packetgen eth3d`; the manifest's
            # frames are downsampled copies, so SfM on them would replace better truth.
            results.append({"house_id": hid, "status": "built_from_ground_truth"})
            continue
        loc = entry.get("location") if isinstance(entry.get("location"), dict) else {}
        lat, lon = first(entry, "lat", "latitude", default=loc.get("lat", loc.get("latitude"))), first(entry, "lon", "lng", "longitude", default=loc.get("lon", loc.get("lng", loc.get("longitude"))))
        anchor = {"latitude": float(lat), "longitude": float(lon)} if lat is not None and lon is not None else None
        source = source_label(entry, str(folder))
        notes = str(first(entry, "notes", "description", default=""))
        seq_frames = None
        frames_dir = folder / "frames"
        calib = entry.get("calibration") if isinstance(entry.get("calibration"), dict) else {}
        if kind in STATIC_KINDS:
            photos = pick_static_stills(entry, list_images(frames_dir) if frames_dir.exists() else images)
            info = build_photo_only(hid, photos, out_root, source_dataset=source, notes=notes,
                                    purposes=still_purposes(entry))
            info.update(source_fingerprint=fp, manifest_entry=entry)
            (out_root / hid / "house.json").write_text(json.dumps(info, indent=1, default=str) + "\n")
            results.append({"house_id": hid, "status": "built", "variants": list(info["variants"])})
            continue
        if kind == "gt_dataset" and calib.get("type") == "colmap_text" and (folder / calib.get("dir", "calibration")).exists():
            try:
                recon = from_colmap_model(folder / calib.get("dir", "calibration"), frames_dir, metric=True,
                                          source="dataset_ground_truth")
                scan = load_scan(hid, work_root, log)
                log(f"{hid}: metric ground-truth model, {len(recon.views)} views, scan {'yes' if scan is not None else 'no'}")
                info = HouseBuilder(hid, recon, out_root, anchor=anchor, scan_points_src=scan,
                                    source_dataset=source, notes=notes).build()
                info.update(source_fingerprint=fp, manifest_entry=entry)
                (out_root / hid / "house.json").write_text(json.dumps(info, indent=1, default=str) + "\n")
                results.append({"house_id": hid, "status": "built", "variants": list(info["variants"])})
            except Exception as e:
                results.append({"house_id": hid, "status": "error", "error": repr(e), "trace": traceback.format_exc()[-2000:]})
                log(f"{hid}: ERROR {e!r}")
            continue
        if videos and (kind in SEQUENCE_KINDS or not images):
            seq_frames = []
            for v in videos:
                seq_frames += extract_video_frames(v, work_root / hid / "video_frames")
            seq_dir = work_root / hid / "video_frames"
        elif frames_dir.exists() and list_images(frames_dir):
            seq_dir, seq_frames = frames_dir, list_images(frames_dir)
        elif kind in SEQUENCE_KINDS or (kind not in PHOTO_KINDS and looks_sequential(images)):
            seq_dir, seq_frames = folder, images
        try:
            info = None
            if seq_frames and len(seq_frames) >= 5:
                log(f"{hid}: sequence of {len(seq_frames)} frames, running SfM")
                try:
                    norm = normalize_images(seq_dir, work_root / hid / "images")
                    model = run_sfm(norm, work_root / hid, sequential=len(seq_frames) > 80)
                    recon = from_colmap_model(model, norm, metric=False, source="pycolmap_sfm")
                    names = {p.name for p in list_images(norm)}
                    recon.dropped = sorted(names - {v.name for v in recon.views})
                    if len(recon.views) >= max(5, 0.3 * len(names)):
                        hb = HouseBuilder(hid, recon, out_root, anchor=anchor, source_dataset=source, notes=notes)
                        info = hb.build()
                    else:
                        log(f"{hid}: SfM registered {len(recon.views)}/{len(names)}; falling back to photo-only")
                except Exception as e:  # SfM failure on hard footage is expected; fall back
                    log(f"{hid}: SfM failed ({e}); falling back to photo-only")
            if info is None:
                photos = images or seq_frames or []
                if not photos:
                    results.append({"house_id": hid, "status": "no_images"})
                    continue
                info = build_photo_only(hid, photos, out_root, source_dataset=source, notes=notes)
            info["source_fingerprint"] = fp
            info["manifest_entry"] = entry
            (out_root / hid / "house.json").write_text(json.dumps(info, indent=1, default=str) + "\n")
            results.append({"house_id": hid, "status": "built", "variants": list(info["variants"])})
        except Exception as e:
            results.append({"house_id": hid, "status": "error", "error": repr(e), "trace": traceback.format_exc()[-2000:]})
            log(f"{hid}: ERROR {e!r}")
    write_index(out_root)
    return results


def write_index(out_root: Path) -> None:
    houses = []
    for hj in sorted(out_root.glob("*/house.json")):
        h = json.loads(hj.read_text())
        houses.append({k: h.get(k) for k in ("house_id", "source_dataset", "keyframes", "stills", "variants", "metric_truth",
                                             "scale_source", "scale_rel_sd", "depth_source", "path_length_m", "notes")})
    (out_root / "index.json").write_text(json.dumps({"generator": "packetgen", "houses": houses}, indent=1) + "\n")


__all__ = ["run_manifest", "write_index", "IMAGE_EXTS"]
