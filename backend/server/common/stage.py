"""Keep ``server.<stage>.run`` bound to the stage function.

Importing the ``run`` submodule stores that module on the package. The
coordinator calls the function. ``export_run`` makes attribute lookup return
the function after that import.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from types import ModuleType


def export_run(fn: Callable) -> None:
    package_name = fn.__module__.rsplit(".", 1)[0]
    package = sys.modules[package_name]
    package._hss_stage_run = fn  # type: ignore[attr-defined]

    class _StagePackage(ModuleType):
        def __getattribute__(self, name: str):
            if name == "run":
                return object.__getattribute__(self, "_hss_stage_run")
            return super().__getattribute__(name)

    package.__class__ = _StagePackage
