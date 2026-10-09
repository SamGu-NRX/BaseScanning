"""The module-level contract every model runner in `models/` satisfies.

`models.run` resolves `--model` to a module name through `MODULES` and imports it dynamically,
because the model packages import torch at module load. After the import the module is used only
through this interface, so a runner that drifts from it fails here, at the boundary, instead of
halfway through a run. `runtime_checkable` lets tests assert each module exposes the interface.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from models.common import ImageResult, RunInputs


@runtime_checkable
class ModelModule(Protocol):
    """A runner module: pinning metadata, `load(device) -> model`, `run(model, inputs) -> results`.

    `load` returns an opaque model object, and each module's `run` accepts exactly what its own
    `load` returns; the parameter type here is `object` because every model class belongs to one
    runner. Runners narrow that object at their own boundary and fail loudly if it is not what
    `load` returned.
    """

    REPO: str
    FILENAME: str
    REVISION: str
    SHA256: str
    LICENSE: str
    CODE: dict[str, str]

    def load(self, device: str) -> object: ...

    def run(self, model: object, inputs: RunInputs) -> Iterable[ImageResult]: ...
