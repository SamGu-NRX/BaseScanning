"""EXIF: read what a source photo carries, synthesize what an iPhone would write, and derive
nominal intrinsics from the 35 mm-equivalent focal length."""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import numpy as np
from PIL import ExifTags, Image

EXIF_KEYS = (
    "Make", "Model", "LensModel", "ExposureTime", "ISOSpeedRatings", "FNumber", "FocalLength",
    "FocalLenIn35mmFilm", "BrightnessValue", "ExposureBiasValue", "DateTimeOriginal",
    "SubsecTimeOriginal", "OffsetTimeOriginal", "Orientation", "PixelXDimension", "PixelYDimension",
)
TAGS = {v: k for k, v in ExifTags.TAGS.items()}
FULL_FRAME_DIAGONAL_MM = math.hypot(36.0, 24.0)


def _plain(v):
    if isinstance(v, bytes):
        return v.decode(errors="ignore").strip("\x00 ")
    if hasattr(v, "numerator") and hasattr(v, "denominator"):
        return float(v.numerator) / float(v.denominator) if v.denominator else None
    if isinstance(v, tuple):
        return [_plain(x) for x in v]
    return v


def read_exif(path: Path) -> tuple[dict, dict | None]:
    """(EXIF subset with CGImageProperties-style key names, GPS dict or None)."""
    im = Image.open(path)
    raw = im.getexif()
    out: dict = {}
    merged = dict(raw)
    try:
        merged.update(raw.get_ifd(0x8769))  # Exif sub-IFD
    except Exception:
        pass
    for key in EXIF_KEYS:
        tag = TAGS.get(key if key != "ISOSpeedRatings" else "ISOSpeedRatings")
        if tag is None and key == "ISOSpeedRatings":
            tag = TAGS.get("PhotographicSensitivity")
        if tag in merged:
            val = _plain(merged[tag])
            if val not in (None, "", []):
                out[key] = val
    gps = None
    try:
        g = raw.get_ifd(0x8825)
        if g:
            def deg(vals, ref):
                d, m, s = (_plain(x) for x in vals)
                v = d + m / 60 + s / 3600
                return -v if ref in ("S", "W") else v
            gps = {"latitude": deg(g[2], g[1]), "longitude": deg(g[4], g[3])}
            if 6 in g:
                gps["altitude"] = _plain(g[6]) * (-1 if g.get(5) == 1 else 1)
    except Exception:
        gps = None
    return out, gps


def focal35_from_K(fx: float, w: int, h: int) -> float:
    """35 mm-equivalent focal length by the diagonal convention."""
    return fx * FULL_FRAME_DIAGONAL_MM / math.hypot(w, h)


def K_from_focal35(f35: float, w: int, h: int) -> tuple[float, float, float, float]:
    f = f35 * math.hypot(w, h) / FULL_FRAME_DIAGONAL_MM
    return (f, f, w / 2.0, h / 2.0)


def synth_exposure(lum: float, rng: np.random.Generator, f_number: float = 1.78) -> dict:
    """Plausible outdoor phone exposure for an image of mean luminance lum (0..1).

    APEX: Ev = Av + Tv = Bv + Sv, Av = 2 log2 N, Tv = -log2 t, Sv = log2(ISO / 3.125)."""
    ev100 = float(np.clip(10.5 + 12.0 * (lum - 0.35) + rng.normal(0, 0.4), 7.0, 15.5))
    iso = 32 if ev100 > 13 else 50 if ev100 > 11.5 else 100 if ev100 > 10 else 200 if ev100 > 8.5 else 400
    t = f_number**2 / (2**ev100 * iso / 100.0)
    t = float(np.clip(t, 1 / 8000, 1 / 15))
    bv = 2 * math.log2(f_number) - math.log2(t) - math.log2(iso / 3.125)
    return {"ExposureTime": t, "ISOSpeedRatings": [iso], "FNumber": f_number, "BrightnessValue": round(bv, 3),
            "ExposureBiasValue": 0.0}


def exif_datetime(t: dt.datetime) -> dict:
    local = t.astimezone(dt.timezone(dt.timedelta(hours=-5)))
    return {
        "DateTimeOriginal": local.strftime("%Y:%m:%d %H:%M:%S"),
        "SubsecTimeOriginal": f"{local.microsecond // 1000:03d}",
        "OffsetTimeOriginal": "-05:00",
    }
