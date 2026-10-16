"""Shared paths and helpers for the A3 (metric monocular depth + fusion) breadth path.

ETH3D loading, visibility and pair metrics are reused from PR #12 (`experiments/evals/evals/` on
branch t3/evals of SamGu-NRX/house-scanning), imported from a read-only clone. Conventions follow it:
OpenCV cameras (+x right, +y down, +z forward), pixel centres on integers, 4x4 camera-to-world poses
in meters, depth as z along the optical axis.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("A3_ROOT", Path.home() / "a3"))
os.environ.setdefault("HOUSE_SCANNING_DATA", str(ROOT / "data"))
PR12 = ROOT / "hs" / "experiments" / "evals"
if str(PR12) not in sys.path:
    sys.path.insert(0, str(PR12))
PACKETGEN = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/packet-generator")
if str(PACKETGEN) not in sys.path:
    sys.path.insert(0, str(PACKETGEN))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from evals.eth3d import View, read_views, resized_image  # noqa: E402,F401
from evals.paths import ETH3D_DIR  # noqa: E402

WORK = ROOT / "work"
PRED = WORK / "pred"
WIDTH = 1024
SCENES = ("electro", "delivery_area", "facade")
INCH = 0.0254
FOOT = 0.3048


def scene_views(scene: str) -> list[View]:
    return read_views(ETH3D_DIR / scene)


def eval_image(view: View) -> tuple[np.ndarray, np.ndarray]:
    """RGB image at WIDTH and its OpenCV intrinsics (the dataset's, rescaled)."""
    bgr, w, h = resized_image(view, WIDTH)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), view.scaled_K(w, h)


def load_depth(scene: str, model: str, name: str) -> tuple[np.ndarray, dict]:
    z = np.load(PRED / scene / model / f"{name}.npz")
    d = z["depth"].astype(np.float32)
    if "valid" in z:
        d = np.where(z["valid"], d, np.nan)
    meta = {k: z[k] for k in z.files if k not in ("depth", "valid")}
    return d, meta
