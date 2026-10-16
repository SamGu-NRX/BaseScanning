"""EXIF focal length to intrinsics. Port of packetgen.exif without the generator helpers."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import ExifTags, Image

EXIF_KEYS = (
    "Make",
    "Model",
    "LensModel",
    "ExposureTime",
    "ISOSpeedRatings",
    "FNumber",
    "FocalLength",
    "FocalLenIn35mmFilm",
    "BrightnessValue",
    "ExposureBiasValue",
    "DateTimeOriginal",
    "SubsecTimeOriginal",
    "OffsetTimeOriginal",
    "Orientation",
    "PixelXDimension",
    "PixelYDimension",
)
TAGS = {v: k for k, v in ExifTags.TAGS.items()}
FULL_FRAME_DIAGONAL_MM = math.hypot(36.0, 24.0)


def _plain(value):
    if isinstance(value, bytes):
        return value.decode(errors="ignore").strip("\x00 ")
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        return float(value.numerator) / float(value.denominator) if value.denominator else None
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def read_exif(path: Path) -> tuple[dict, dict | None]:
    """(EXIF subset with CGImageProperties-style names, GPS dict or None)."""
    image = Image.open(path)
    raw = image.getexif()
    merged = dict(raw)
    try:
        merged.update(raw.get_ifd(0x8769))
    except Exception:
        pass
    out: dict = {}
    for key in EXIF_KEYS:
        tag = TAGS.get(key)
        if tag is None and key == "ISOSpeedRatings":
            tag = TAGS.get("PhotographicSensitivity")
        if tag in merged:
            val = _plain(merged[tag])
            if val not in (None, "", []):
                out[key] = val
    gps = None
    try:
        gps_ifd = raw.get_ifd(0x8825)
        if gps_ifd:

            def deg(vals, ref):
                d, m, s = (_plain(x) for x in vals)
                v = d + m / 60 + s / 3600
                return -v if ref in ("S", "W") else v

            gps = {"latitude": deg(gps_ifd[2], gps_ifd[1]), "longitude": deg(gps_ifd[4], gps_ifd[3])}
    except Exception:
        gps = None
    return out, gps


def focal35_from_K(fx: float, w: int, h: int) -> float:
    return fx * FULL_FRAME_DIAGONAL_MM / math.hypot(w, h)


def K_from_focal35(f35: float, w: int, h: int) -> tuple[float, float, float, float]:
    focal = f35 * math.hypot(w, h) / FULL_FRAME_DIAGONAL_MM
    focal = round(focal, 3)
    return (focal, focal, round(w / 2.0, 3), round(h / 2.0, 3))


def _f35(exif: dict | None) -> float | None:
    if not exif:
        return None
    value = exif.get("FocalLenIn35mmFilm")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_still_intrinsics(still: dict, keyframes: dict[str, dict], file_exif: dict | None, params) -> dict:
    """Decide the still's intrinsics. `apply` is the replacement K, or None to keep the packet."""
    source = still.get("intrinsicsSource")
    if source in {"arkit", "calibration"} and still.get("intrinsics"):
        return {"keep": True, "warnings": [], "intrinsicsAssumed": False, "focalRelBound": params.focal_rel_bound_read}
    if still.get("keyframe") and not still.get("intrinsics"):
        frame = keyframes.get(still["keyframe"])
        if frame and frame.get("intrinsics"):
            return {
                "keep": False,
                "intrinsics": list(frame["intrinsics"]),
                "intrinsicsSource": "arkit",
                "warnings": [],
                "intrinsicsAssumed": False,
                "focalRelBound": params.focal_rel_bound_read,
            }
    file_f35 = _f35(file_exif)
    packet_f35 = _f35(still.get("exif"))
    warnings: list[tuple[str, str]] = []
    assumed = False
    if not file_exif and packet_f35 is None and source != "exif_focal35":
        warnings.append(("exif_missing", "still has no EXIF"))
    if file_f35 is not None and packet_f35 is not None and abs(file_f35 - packet_f35) > 0.51:
        warnings.append(("exif_mismatch", "file FocalLenIn35mmFilm differs from the packet; file wins"))
    f35 = file_f35 if file_f35 is not None else packet_f35
    if f35 is None:
        f35 = params.focal_assumed_mm
        assumed = True
        warnings.append(("intrinsics_assumed", f"assumed {f35:.0f} mm"))
    if file_exif and file_exif.get("Make") not in (None, "Apple"):
        warnings.append(("non_apple_photo", f"Make is {file_exif.get('Make')}"))
    if not (params.f35_range_mm[0] <= f35 <= params.f35_range_mm[1]):
        warnings.append(("exif_focal_out_of_range", f"focal length {f35} mm is outside 10–200"))
        # The issue table lists this one as an error. Caller promotes it.
    elif not (params.f35_wide_mm[0] <= f35 <= params.f35_wide_mm[1]):
        warnings.append(("non_wide_lens", f"focal length {f35} mm is not the 1x camera"))
    try:
        w, h = int(still["w"]), int(still["h"])
    except (KeyError, TypeError, ValueError):
        return {
            "keep": True,
            "warnings": warnings,
            "intrinsicsAssumed": assumed,
            "focalRelBound": params.focal_rel_bound_assumed if assumed else params.focal_rel_bound_read,
        }
    recomputed = list(K_from_focal35(f35, w, h))
    existing = still.get("intrinsics")
    if source == "exif_focal35" and existing and not assumed:
        try:
            old = float(existing[0])
            if old and abs(recomputed[0] - old) / abs(old) > params.intrinsics_recompute_frac:
                warnings.append(("intrinsics_recomputed", "exif_focal35 intrinsics replaced"))
            else:
                return {
                    "keep": True,
                    "warnings": warnings,
                    "intrinsicsAssumed": False,
                    "focalRelBound": params.focal_rel_bound_read,
                }
        except (TypeError, ValueError, IndexError):
            pass
    return {
        "keep": False,
        "intrinsics": recomputed,
        "intrinsicsSource": "exif_focal35",
        "warnings": warnings,
        "intrinsicsAssumed": assumed,
        "focalRelBound": params.focal_rel_bound_assumed if assumed else params.focal_rel_bound_read,
    }
