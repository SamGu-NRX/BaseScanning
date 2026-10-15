"""Prepare a still for a vision call. No crop, sharpen, tile, or contrast change."""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageOps


def prepare(data: bytes, max_side_px: int) -> tuple[bytes, str, int, int]:
    """Return JPEG bytes, a data URL, and the size after a long-side cap. Never upscales."""
    with Image.open(io.BytesIO(data)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    image.thumbnail((max_side_px, max_side_px))
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=90)
    jpeg = buf.getvalue()
    encoded = base64.b64encode(jpeg).decode("ascii")
    return jpeg, "data:image/jpeg;base64," + encoded, image.width, image.height


def open_rgb(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as opened:
        return ImageOps.exif_transpose(opened).convert("RGB")
