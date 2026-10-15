"""Pose matrices and the scale-file reader shared by dense, coverage and the pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def as_pose(pose) -> np.ndarray:
    arr = np.asarray(pose, float)
    if arr.shape == (4, 4):
        return arr
    return arr.reshape(4, 4, order="F")


def load_scale(path, flow: str = "guided") -> tuple[dict, list[str]]:
    warnings: list[str] = []
    if path is None or not Path(path).is_file():
        warnings.append("scale_missing")
        if flow == "photo":
            return {
                "source": "none",
                "status": "unverified",
                "rel_bound_95": 1.0,
                "scale_factor": 1.0,
                "origin_m": None,
            }, warnings
        return {
            "source": "arkit",
            "status": "unverified",
            "rel_bound_95": 0.09,
            "scale_factor": 1.0,
            "origin_m": None,
        }, warnings
    return json.loads(Path(path).read_text()), warnings
