"""Photo-only packets for single, unordered photos (listing photos, close-ups): EXIF only.

Pixels are kept upright as the source shows them (orientation 1), capped at 4032 px on the long
side. EXIF comes from the file when present. When absent it is synthesized as an iPhone would
write it, and every synthesized key is listed in `source.synthesizedFields`; the 35 mm focal
length then defaults to the 1x wide camera (26 mm), an assumption, not a measurement.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from . import __version__
from .devices import DEVICES
from .exif import K_from_focal35, exif_datetime, read_exif, synth_exposure
from .images import luminance, open_upright, save_jpeg
from .packet import CONVENTIONS, FORMAT_VERSION, UNITS, FileHasher, iso
from .sources import list_images

MAX_LONG_SIDE = 4032
DEFAULT_FOCAL35 = 26


def build_photo_only(house_id: str, images: list[Path], out_root: Path, source_dataset: str = "",
                     purposes: dict[str, str] | None = None, notes: str = "") -> dict:
    d = out_root / house_id
    (d / "stills").mkdir(parents=True, exist_ok=True)
    seed = int(hashlib.sha256(house_id.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    hasher = FileHasher(d)
    dev = DEVICES["nonlidar_modern"]
    start_wall = dt.datetime(2026, 9, 26, 15, 0, tzinfo=dt.timezone.utc)
    start_uptime = float(rng.uniform(20000, 200000))
    stills, files, synthesized, truth = [], [], set(), []
    t = start_uptime + 5.0
    for i, src in enumerate(images):
        exif, gps = read_exif(src)
        im = open_upright(src)
        if max(im.size) > MAX_LONG_SIDE:
            s = MAX_LONG_SIDE / max(im.size)
            im = im.resize((round(im.size[0] * s), round(im.size[1] * s)), Image.LANCZOS)
        sid = f"s{i + 1:05d}"
        rel = f"stills/{sid}.jpg"
        save_jpeg(im, d / rel, quality=92)
        w, h = im.size
        e = dict(exif)
        e.pop("Orientation", None)
        e["PixelXDimension"], e["PixelYDimension"] = w, h
        e["Orientation"] = 1
        if "FocalLenIn35mmFilm" not in e:
            e["FocalLenIn35mmFilm"] = DEFAULT_FOCAL35
            synthesized.add("stills[].exif.FocalLenIn35mmFilm (assumed 26 mm when the source had none)")
        if "ExposureTime" not in e:
            e.update(synth_exposure(luminance(im), rng, dev["fNumber"]))
            synthesized.add("stills[].exif exposure keys (ExposureTime, ISOSpeedRatings, FNumber, BrightnessValue)")
        if "Make" not in e:
            e.update({"Make": "Apple", "Model": dev["marketingName"], "LensModel": dev["lens"], "FocalLength": dev["focalLengthMm"]})
            synthesized.add("stills[].exif Make/Model/LensModel/FocalLength")
        if "DateTimeOriginal" not in e:
            e.update(exif_datetime(start_wall + dt.timedelta(seconds=t - start_uptime)))
            synthesized.add("stills[].exif DateTimeOriginal")
        e = {k: v for k, v in e.items() if not isinstance(v, (list, tuple)) or k == "ISOSpeedRatings"}
        if "ISOSpeedRatings" in e and not isinstance(e["ISOSpeedRatings"], list):
            e["ISOSpeedRatings"] = [e["ISOSpeedRatings"]]
        still = {
            "id": sid, "purpose": (purposes or {}).get(src.name, "house_overview"), "img": rel, "w": w, "h": h,
            "timestamp": round(t, 4), "orientation": 1,
            "intrinsics": [round(x, 3) for x in K_from_focal35(float(e["FocalLenIn35mmFilm"]), w, h)],
            "intrinsicsSource": "exif_focal35", "exif": e,
        }
        if gps:
            still["location"] = {"latitude": gps["latitude"], "longitude": gps["longitude"]}
        stills.append(still)
        files.append(hasher.entry(rel, "still"))
        truth.append({"id": sid, "source": str(src), "had_exif": bool(exif), "had_gps": bool(gps)})
        t += float(rng.uniform(3, 20))
    packet = {
        "format": "house-capture-packet", "formatVersion": FORMAT_VERSION,
        "packetId": str(uuid.uuid4()), "createdAt": iso(dt.datetime.now(dt.timezone.utc)),
        "units": {k: UNITS[k] for k in ("length", "angle", "time", "image")},
        "conventions": {k: CONVENTIONS[k] for k in ("world", "camera", "pose", "intrinsics", "pixel")},
        "source": {"kind": "synthetic", "generator": f"packetgen {__version__}", "variant": "photo-only",
                   "noiseProfile": "none", "sourceDataset": source_dataset,
                   "synthesizedFields": sorted(synthesized) + ["stills[].timestamp", "stills[].intrinsics (from the 35 mm focal length)"],
                   "notes": (notes + " Single photos: EXIF-only metadata.").strip()},
        "session": {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"packetgen/{house_id}/photo-only")).upper(),
            "startedAt": iso(start_wall), "startedAtUptime": round(start_uptime, 4),
            "appVersion": f"packetgen-synthetic {__version__}", "deviceModel": dev["deviceModel"],
            "systemVersion": dev["systemVersion"], "tier": "photo_only", "lidarAvailable": False, "sceneDepthEnabled": False,
        },
        "keyframes": [], "stills": stills, "files": files,
    }
    (d / "packet.photo-only.json").write_text(json.dumps(packet, indent=1) + "\n")
    (d / "_truth").mkdir(exist_ok=True)
    (d / "_truth" / "truth.json").write_text(json.dumps({"house_id": house_id, "photos": truth}, indent=1) + "\n")
    info = {"house_id": house_id, "generator": f"packetgen {__version__}", "source_dataset": source_dataset,
            "stills": len(stills), "variants": {"photo-only": "packet.photo-only.json"}, "truth": "_truth/truth.json", "notes": notes}
    (d / "house.json").write_text(json.dumps(info, indent=1) + "\n")
    return info
