"""CPU image pins until WS-12's ``server.modal_app.CPU_IMAGE`` exists, then that object is re-exported.

Pins match the brief §5 list. This module does not build a Modal image when the SDK is absent.
"""

from __future__ import annotations

CPU_PINS: tuple[str, ...] = (
    "python==3.12",
    "torch",
    "open_clip_torch",
    "transformers",
    "opencv-python-headless==5.0.0.93",
    "pillow==12.3.0",
    "numpy==2.5.3",
    "openai",
    "zxing-cpp",
    "pydantic>=2.13.5",
)


def cpu_image():
    """WS-12's image when ``server.modal_app`` exports one, otherwise ``None``."""
    try:
        from server.modal_app import CPU_IMAGE
    except ImportError:
        return None
    return CPU_IMAGE
