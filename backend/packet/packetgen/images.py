"""Turn a source photo into what ARKit hands the app: an unrotated landscape sensor frame at an
ARKit video-format resolution, with intrinsics for exactly those pixels."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .conventions import portrait_to_landscape
from .sources import list_images

# ARWorldTrackingConfiguration.supportedVideoFormats on current iPhones include 1920x1440 (4:3)
# and 1920x1080 (16:9); 1920x1440 is the usual default.
ARKIT_FORMATS = {"4:3": (1920, 1440), "16:9": (1920, 1080)}


@dataclass
class PreparedFrame:
    size: tuple[int, int]
    K: tuple[float, float, float, float]
    T_cv: np.ndarray  # camera-to-world (OpenCV) for the prepared pixels
    rotated_from_portrait: bool
    crop_box: tuple[int, int, int, int]  # in the (landscape) source, before resizing
    scale: tuple[float, float]
    arkit_format: str


def open_upright(path: Path) -> Image.Image:
    im = Image.open(path)
    return ImageOps.exif_transpose(im).convert("RGB")


def choose_format(w: int, h: int) -> tuple[str, tuple[int, int]]:
    a = w / h
    if 1.2 <= a <= 1.55:
        return "4:3", ARKIT_FORMATS["4:3"]
    if 1.6 <= a <= 1.95:
        return "16:9", ARKIT_FORMATS["16:9"]
    long = 1920
    return "nonstandard", (long, int(round(long / a)))


def prepare(image: Image.Image, K, T_cv: np.ndarray) -> tuple[Image.Image, PreparedFrame]:
    """`image` is the upright image the intrinsics K and pose T_cv describe."""
    w, h = image.size
    rotated = False
    if h > w:
        T_cv, K = portrait_to_landscape(T_cv, K, portrait_width=w)
        image = image.rotate(90, expand=True)  # PIL rotates counter-clockwise
        w, h = image.size
        rotated = True
    fmt, (W, H) = choose_format(w, h)
    target = W / H
    if w / h > target:
        cw, ch = int(round(h * target)), h
    else:
        cw, ch = w, int(round(w / target))
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    image = image.crop((x0, y0, x0 + cw, y0 + ch)).resize((W, H), Image.LANCZOS)
    sx, sy = W / cw, H / ch
    fx, fy, cx, cy = K
    K2 = (fx * sx, fy * sy, (cx - x0) * sx, (cy - y0) * sy)
    return image, PreparedFrame((W, H), K2, T_cv, rotated, (x0, y0, x0 + cw, y0 + ch), (sx, sy), fmt)


def save_jpeg(image: Image.Image, path: Path, quality: int = 90) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=quality, optimize=True)


def normalize_images(src: Path, dst: Path) -> Path:
    """Upright copies (EXIF orientation applied), so SfM and packets see the same pixels."""
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for p in list_images(src):
        save_jpeg(open_upright(p), dst / (p.stem + ".jpg"), quality=95)
    return dst


def luminance(image: Image.Image) -> float:
    small = np.asarray(image.convert("L").resize((64, 48)), float) / 255.0
    return float(small.mean())


def sharpness(image: Image.Image) -> float:
    """Variance of a Laplacian on a 480 px grey copy: higher is sharper."""
    g = np.asarray(image.convert("L").resize((480, int(480 * image.size[1] / image.size[0]))), float)
    lap = -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    return float(lap.var())
