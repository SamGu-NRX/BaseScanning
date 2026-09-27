"""Bind the vendored eval harness in ``server.eval``."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

_CACHE: SimpleNamespace | None = None


def load() -> SimpleNamespace:
    """Scene I/O, coverage grading, frame math, the chain adapter, and verdict scoring."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    visibility = importlib.import_module("server.eval.visibility")
    _CACHE = SimpleNamespace(
        sceneio=importlib.import_module("server.eval.sceneio"),
        coverage=importlib.import_module("server.eval.coverage"),
        frame=importlib.import_module("server.eval.frame"),
        verdictscore=importlib.import_module("server.eval.verdictscore"),
        hs11=importlib.import_module("server.eval.adapters.hs11"),
        camera=visibility.Camera,
    )
    return _CACHE
