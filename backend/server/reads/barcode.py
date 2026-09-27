"""zxing-cpp over the full image and a half-size copy. Payloads are a precision source only."""

from __future__ import annotations

import logging
import os
import re

import numpy as np
from PIL import Image

from server.reads.candidates import core, digits

try:
    import zxingcpp
except ImportError:
    zxingcpp = None

log = logging.getLogger("server.reads")


def read_barcodes(image: Image.Image) -> list[dict]:
    """Unique payloads with normalised boxes.

    A missing zxing-cpp install is an error outside pytest. Tests may run without the
    `reads` extra and then get an empty list after the error is logged.
    """
    if zxingcpp is None:
        log.error("zxingcpp is not installed; install the reads extra")
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return []
        raise RuntimeError("zxingcpp is not installed")
    found: dict[str, dict] = {}
    width, height = image.size
    for scale in (1.0, 0.5):
        img = image if scale == 1.0 else image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
        for symbol in zxingcpp.read_barcodes(np.asarray(img.convert("L"))):
            if not symbol.text or symbol.text in found:
                continue
            position = symbol.position
            xs = [
                position.top_left.x,
                position.top_right.x,
                position.bottom_left.x,
                position.bottom_right.x,
            ]
            ys = [
                position.top_left.y,
                position.top_right.y,
                position.bottom_left.y,
                position.bottom_right.y,
            ]
            sw, sh = img.size
            found[symbol.text] = {
                "payload": symbol.text,
                "symbology": str(symbol.format).split(".")[-1],
                "box": [
                    min(xs) / sw,
                    min(ys) / sh,
                    (max(xs) - min(xs)) / sw,
                    (max(ys) - min(ys)) / sh,
                ],
            }
    return list(found.values())


def payload_tokens(barcodes: list[dict]) -> set[str]:
    """Cores of the digit-bearing parts of every decoded payload."""
    parts: set[str] = set()
    for barcode in barcodes:
        for part in re.split(r"[\s;*,{}]+", barcode.get("payload") or ""):
            if digits(part) >= 4:
                parts.add(core(part))
    return parts
