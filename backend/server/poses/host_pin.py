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
If pycolmap is already in ``sys.modules``, that load already chose a core, so
the pin does not apply: this module logs a warning and
``reported_blas_coretype`` returns ``unpinned`` instead of ``HASWELL``.

Poses shares ``run_stage_impl`` and the CPU and GPU images with the other
stages, so these variables are not set on the image.
"""

from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)

OPENBLAS_CORETYPE = "HASWELL"
OPENBLAS_THREADS = "1"
UNPINNED_CORETYPE = "unpinned"
_ENV_KEYS = ("OPENBLAS_NUM_THREADS", "GOTO_NUM_THREADS", "OPENBLAS_CORETYPE")
# True only when this process loaded pycolmap inside the pin window.
PIN_APPLIED = False


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


def reported_blas_coretype() -> str:
    """Core a registration report may claim. ``HASWELL`` only if the pin loaded pycolmap."""
    if PIN_APPLIED:
        return OPENBLAS_CORETYPE
    return UNPINNED_CORETYPE


def reported_blas_threads() -> int | None:
    """Thread count the pin actually installed. ``None`` when pycolmap was already loaded."""
    if PIN_APPLIED:
        return int(OPENBLAS_THREADS)
    return None


# The extension reads the variables at load. Restore before this module returns
# so a warm container's numpy stays on its own thread count. A prior import
# already chose its core; setting the variables then would not change it.
# isort: off
if "pycolmap" in sys.modules:
    _pycolmap = sys.modules["pycolmap"]
    log.warning("pycolmap is already imported; the OpenBLAS pin did not apply")
else:
    _previous_numeric_host = pin_numeric_host()
    try:
        import pycolmap as _pycolmap  # noqa: E402
    finally:
        restore_numeric_host(_previous_numeric_host)
    PIN_APPLIED = True
# isort: on

PYCOLMAP_LOADED = _pycolmap.__name__
