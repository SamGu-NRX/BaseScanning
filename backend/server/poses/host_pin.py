"""Pin pycolmap's OpenBLAS for the poses stage only.

Two AVX-512 VMs on the same pycolmap 4.2.0 wheel built different facade models
(66,770 vs 66,152 points) after mapping was already one thread. Unpinned,
OpenBLAS selects Cooperlake on both. The kernels still do not agree in the
last bits. FAISS L2 in this wheel has no env switch (``FAISS_OPT_LEVEL`` is
absent), so matching stays on FAISS and WS-11 pins the gate host by instance
type (``ws-11-ci``) instead.

Importing this module sets ``OPENBLAS_NUM_THREADS=1``, ``GOTO_NUM_THREADS=1``,
and ``OPENBLAS_CORETYPE=HASWELL``, imports pycolmap, then restores the
previous environment. OpenBLAS reads those variables when its copy inside
pycolmap loads. Restoring them keeps numpy multi-threaded for later stages.

Poses shares ``run_stage_impl`` and the CPU and GPU images with the other
stages, so these variables are not set on the image.
"""

from __future__ import annotations

import os

OPENBLAS_CORETYPE = "HASWELL"
OPENBLAS_THREADS = "1"
_ENV_KEYS = ("OPENBLAS_NUM_THREADS", "GOTO_NUM_THREADS", "OPENBLAS_CORETYPE")


def pin_numeric_host() -> dict[str, str | None]:
    """Set the OpenBLAS pin. Returns the previous values for ``restore_numeric_host``."""
    previous = {key: os.environ.get(key) for key in _ENV_KEYS}
    os.environ["OPENBLAS_NUM_THREADS"] = OPENBLAS_THREADS
    os.environ["GOTO_NUM_THREADS"] = OPENBLAS_THREADS
    os.environ["OPENBLAS_CORETYPE"] = OPENBLAS_CORETYPE
    return previous


def restore_numeric_host(previous: dict[str, str | None]) -> None:
    """Put the process environment back so later stages do not inherit the pin."""
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


# The extension reads the variables at load. Restore before this module returns
# so a warm container's numpy stays on its own thread count.
# isort: off
_previous_numeric_host = pin_numeric_host()
try:
    import pycolmap as _pycolmap  # noqa: E402
finally:
    restore_numeric_host(_previous_numeric_host)
# isort: on

PYCOLMAP_LOADED = _pycolmap.__name__
