"""iOS contract kit. Schemas and examples are the device contract; this package drives them."""

from __future__ import annotations

import sys
from pathlib import Path

__version__ = "0.4.0"

_REPO = Path(__file__).resolve().parents[2]
if (_REPO / "server" / "contracts").is_dir() and str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
