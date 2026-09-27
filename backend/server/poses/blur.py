"""Drop frames that A7 showed break SIFT, before matching.

The measured rule is exposure duration × gyro rate × focal length. Electro
lost registration at about 5.6 px of blur, so the cut is 4 px. When the
packet has no exposure and no gyro, a Laplacian check drops only frames that
are obviously smeared.
"""

from __future__ import annotations

import csv
import gzip
from pathlib import Path

import numpy as np
from PIL import Image

from server.poses.params import PoseParams


def blur_px(duration_s: float, gyro_rad_s: float, focal_px: float) -> float:
    return float(duration_s) * float(gyro_rad_s) * float(focal_px)


def variance_of_laplacian(path: Path) -> float:
    image = Image.open(path).convert("L")
    image.thumbnail((320, 320))
    pixels = np.asarray(image, dtype=np.float32)
    if pixels.shape[0] < 3 or pixels.shape[1] < 3:
        return 0.0
    lap = -4.0 * pixels[1:-1, 1:-1] + pixels[:-2, 1:-1] + pixels[2:, 1:-1] + pixels[1:-1, :-2] + pixels[1:-1, 2:]
    return float(lap.var())


def _open_csv(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", newline="")
    return path.open(newline="")


def gyro_series(path: Path) -> list[tuple[float, float]]:
    """``(t, |omega|)`` from a ``motion`` or ``imuRaw`` CSV."""
    rows: list[tuple[float, float]] = []
    with _open_csv(path) as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return rows
        fields = set(reader.fieldnames)
        for row in reader:
            try:
                t = float(row["t"])
            except (KeyError, TypeError, ValueError):
                continue
            if {"rotX", "rotY", "rotZ"} <= fields:
                wx, wy, wz = (float(row[k] or "nan") for k in ("rotX", "rotY", "rotZ"))
            elif {"gx", "gy", "gz"} <= fields:
                wx, wy, wz = (float(row[k] or "nan") for k in ("gx", "gy", "gz"))
            else:
                continue
            if not np.isfinite([wx, wy, wz]).all():
                continue
            rows.append((t, float(np.hypot(wx, np.hypot(wy, wz)))))
    rows.sort()
    return rows


def gyro_at(series: list[tuple[float, float]], t: float) -> float | None:
    if not series:
        return None
    times = [s[0] for s in series]
    i = int(np.searchsorted(times, t))
    i = min(max(i, 0), len(series) - 1)
    prev = series[i - 1] if i else series[0]
    nxt = series[i]
    if abs(t - prev[0]) <= abs(nxt[0] - t):
        return prev[1]
    return nxt[1]


def frame_blur_px(
    *,
    duration_s: float | None,
    gyro_rad_s: float | None,
    focal_px: float,
    image: Path | None,
    params: PoseParams,
) -> tuple[float | None, str]:
    """Return ``(blur_px or laplacian stand-in, source)``.

    ``source`` is ``imu``, ``laplacian`` or ``none``. A laplacian stand-in is
    negative variance so callers can compare with ``blur_max_px`` only for
    the imu source.
    """
    if duration_s is not None and gyro_rad_s is not None and focal_px > 0:
        return blur_px(duration_s, gyro_rad_s, focal_px), "imu"
    if image is not None and image.is_file():
        return variance_of_laplacian(image), "laplacian"
    return None, "none"


def is_blurred(value: float | None, source: str, params: PoseParams) -> bool:
    if value is None:
        return False
    if source == "imu":
        return value > params.blur_max_px
    if source == "laplacian":
        return value < params.blur_laplacian_min
    return False
