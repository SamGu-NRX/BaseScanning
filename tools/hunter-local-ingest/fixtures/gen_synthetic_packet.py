#!/usr/bin/env python3
"""Repo entry point for the synthetic plan generator.

The generator lives in the package (src/hunter_local_ingest/fixturegen.py)
so an installed copy has it too (``hunter-gen-plan``). This wrapper puts
the checkout's src/ on the import path, so it runs without installing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hunter_local_ingest.fixturegen import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
