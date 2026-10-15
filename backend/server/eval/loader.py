"""Import the vendored geometry harness without pulling a stage package."""

from __future__ import annotations

from types import ModuleType


def load() -> dict[str, ModuleType]:
    from server.eval import chain, coverage, extract, frame, sceneio, visibility

    return {
        "chain": chain,
        "coverage": coverage,
        "extract": extract,
        "frame": frame,
        "sceneio": sceneio,
        "visibility": visibility,
    }
