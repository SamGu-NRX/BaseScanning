"""Phase 0 evaluation harness: scene v0 / verdict v0 contracts and their scorers.

Contract: docs/scene-and-verdict-format.md in the project store.
"""

from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parents[1]
SCHEMA_DIR = HARNESS_DIR / "schemas"
VENDOR_DIR = HARNESS_DIR / "vendor"
