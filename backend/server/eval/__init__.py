"""Phase 0 evaluation harness: scene v0 / verdict v0 contracts and their scorers.

Contract: docs/scene-and-verdict-format.md in the project store.
"""

from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent
_REPO = _PACKAGE.parents[1]
SCHEMA_DIR = _REPO / "server" / "contracts" / "schemas"
VENDOR_DIR = _PACKAGE / "vendor"
