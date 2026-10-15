"""Put the kit ahead of the repo's ``packet/packetgen`` so tests load the vendored validator."""

from __future__ import annotations

import sys
from pathlib import Path

_KIT = str(Path(__file__).resolve().parents[1])
sys.path[:] = [_KIT, *[entry for entry in sys.path if entry != _KIT]]

from ios_kit.vendored import prefer_vendored_packetgen  # noqa: E402


def pytest_configure(config: object) -> None:
    """Re-apply after pytest prepends ``pythonpath`` (``packet/`` shadows the kit)."""
    del config
    prefer_vendored_packetgen()
