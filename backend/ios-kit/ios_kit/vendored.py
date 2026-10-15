"""Load this kit's packetgen, not the repo copy under ``packet/packetgen``.

``pyproject.toml`` puts ``packet/`` on pytest's pythonpath. That copy opens
``.csv.gz`` streams as text. The kit vendors a copy that gunzips them.
"""

from __future__ import annotations

import sys
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent.parent


def prefer_vendored_packetgen() -> None:
    """Put ``ios-kit`` first on ``sys.path`` and drop a shadowed ``packetgen``."""
    kit = str(KIT_ROOT)
    sys.path[:] = [kit, *[entry for entry in sys.path if entry != kit]]
    loaded = sys.modules.get("packetgen")
    origin = str(getattr(loaded, "__file__", "") or "")
    if loaded is not None and not origin.startswith(kit + "/"):
        for name in list(sys.modules):
            if name == "packetgen" or name.startswith("packetgen."):
                del sys.modules[name]


prefer_vendored_packetgen()

from packetgen.validate import validate_packet  # noqa: E402

__all__ = ["prefer_vendored_packetgen", "validate_packet"]
