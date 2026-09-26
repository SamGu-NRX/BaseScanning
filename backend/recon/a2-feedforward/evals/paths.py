"""Where data lives. Datasets are non-commercial and stay out of git; override the root with
HOUSE_SCANNING_DATA if the default is wrong."""

from __future__ import annotations

import os
from pathlib import Path

DATA_ROOT = Path(os.environ.get("HOUSE_SCANNING_DATA", Path.home() / "house-scanning-data"))
EVALS_DIR = DATA_ROOT / "evals"
ADVIO_DIR = EVALS_DIR / "advio"
ETH3D_DIR = EVALS_DIR / "eth3d"
REPLAYS_DIR = DATA_ROOT / "replays"
