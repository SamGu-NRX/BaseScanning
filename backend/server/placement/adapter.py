"""Load the top-level placement solver by explicit file path and run it as a stage.

The top-level `server/` directory — the code behind `POST /v1/placements` — is a flat
module tree, not a package: `units`, `rules`, `scene` and `solver` import each other by
bare names, and resolving those names through `sys.path` is unsafe here because the
backend test path already holds flat modules of the same names under
`eval/vendor/hs11_server/`. The loader therefore never resolves imports from the path:
it reads each file from the repository root by absolute path, registers it in
`sys.modules` under its bare name so the solver's own flat imports bind to the same
loaded modules, and restores the previous entries afterwards. The backend's `server`
package itself is never touched, so nothing in this process can shadow it, and a wrong
import cannot silently succeed — every path is pinned under the repository root.

Stage contract (`run`):
- Inputs (from the run's artifacts, hydrated under the work directory): the merged
  source scene `scene/scene.json` (backend `base-scene/v0` metres — the coordinator's
  `_merge_if_ready` merges the detected objects into it before placement can start)
  and `objects/objects_3d.json` as readiness evidence only. There is no reference
  fallback: a run without these bytes refuses placement rather than solving a scene
  that is not this capture's.
- Readiness gate: the `objects_3d` stage record must be `done` and its artifact must
  carry `format = hss-objects-3d/v0`. A missing, failed or skipped `objects_3d` stage
  refuses placement (`placement_objects_3d_unavailable`, not retryable) — a run whose
  hazard pass did not finish must degrade to manual review, never imply no hazards.
  A successful explicit empty object list is valid.
- Conversion: `placement.scene_bridge.convert_scene` validates the merged `Scene`
  (duplicate ids, unsupported or generated evidence, self-contradictory geometry) and
  converts it to the wire scene exactly once — the adapter never merges objects into
  the scene again. The converted document is serialized deterministically (sorted
  keys, non-finite values forbidden) and those exact bytes are what the unchanged
  solver parses, what `stats.input_sha256` binds to, and what `placement/scene.json`
  publishes. The source scene artifact is never written back.
- Output: `placement/placement.json` — the solver's result verbatim (sorted keys), valid
  against the top-level `schemas/result.schema.json`; `placement/scene.json` — the
  exact converted input bytes. Only `stats.elapsed_ms` varies between runs: the decision
  is deterministic for fixed scene bytes and rules.
- Failure: a missing input artifact raises `PlacementInputError` (code
  `placement_scene_input_missing`); a malformed or unplaceable scene raises
  `PlacementSceneError` (code `placement_scene_invalid`); a scene the bridge cannot
  support raises `SceneBridgeError` carrying `placement_scene_unsupported` or
  `placement_scene_invalid`; a missing solver tree raises `PlacementBoundaryError`
  (code `placement_boundary_missing`). All are not retryable (the bytes do not heal on
  a retry) and are recorded stage data; the optional stage degrades the run instead of
  failing it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any

from server.contracts.jobs import StageContext, StageResult
from server.placement.scene_bridge import convert_scene

# backend/server/placement/adapter.py -> backend/server/placement -> backend/server
# -> backend -> repository root. The top-level placement server lives beside backend/.
_REPO_ROOT = Path(__file__).resolve().parents[3]

# The stage needs these four. Order matters: later modules do flat imports
# (`from rules import ...`) that must bind to the entries loaded first.
_FLAT_STAGE: tuple[str, ...] = ("units", "rules", "scene", "solver")
# The POST /v1/placements app: `siteplan` and `api` join the same flat namespace.
_FLAT_API: tuple[str, ...] = _FLAT_STAGE + ("siteplan", "api")

# Reentrant: load_api takes the lock and then calls load_solver on the same thread.
_LOCK = threading.RLock()
_MODULES: dict[str, ModuleType] | None = None
_API: dict[str, ModuleType] | None = None
_RULES: Any = None


class PlacementBoundaryError(Exception):
    """The top-level solver tree is missing where the stage runs (for example a Modal
    image, which ships only the backend package). Not retryable — the tree will not
    appear on a retry — so the optional stage degrades on the first attempt."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "placement_boundary_missing"


class PlacementSceneError(ValueError):
    """A malformed or unplaceable scene. Not retryable: the bytes do not heal."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "placement_scene_invalid"


class PlacementInputError(Exception):
    """A required source artifact is absent from the run. Not retryable: the stage
    refuses rather than solving a scene that is not this capture's."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "placement_scene_input_missing"


class PlacementObjectsError(Exception):
    """The run's objects_3d pass did not finish, so the scene's hazards are not known
    to be complete. Not retryable: placement refuses instead of implying no hazards."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "placement_objects_3d_unavailable"


def _top_level_server_dir() -> Path:
    return _REPO_ROOT / "server"


def _exec_flat(names: tuple[str, ...]) -> dict[str, ModuleType]:
    """Load flat modules from the top-level server by absolute path.

    Registers each module in `sys.modules` under its bare name before the next one
    executes, so the solver's own `from rules import ...` binds to the module loaded
    here — never to a path lookup, the backend package, or the eval vendor tree.
    Restores whatever held the bare names before, including nothing.
    """

    server_dir = _top_level_server_dir()
    snapshot = {name: sys.modules.get(name) for name in names}
    loaded: dict[str, ModuleType] = {}
    try:
        for name in names:
            path = server_dir / f"{name}.py"
            if not path.is_file():
                raise PlacementBoundaryError(
                    f"{path} is missing: the top-level placement server is not in this "
                    "checkout (Modal images ship only the backend package)"
                )
            spec: ModuleSpec | None = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise PlacementBoundaryError(f"{path} has no importable spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            loaded[name] = module
            spec.loader.exec_module(module)
        return loaded
    finally:
        for name, previous in snapshot.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def load_solver() -> dict[str, ModuleType]:
    """The top-level flat modules, loaded once per process. Thread-safe."""

    global _MODULES
    with _LOCK:
        if _MODULES is None:
            _MODULES = _exec_flat(_FLAT_STAGE)
        return _MODULES


def load_api() -> dict[str, ModuleType]:
    """The stage modules plus `siteplan` and `api` — the live `POST /v1/placements` app.

    Used by the contract test only; the stage path never imports FastAPI.
    """

    global _API
    with _LOCK:
        if _API is None:
            flat = load_solver()
            # The flat names were restored after the stage load; register the loaded
            # objects again while `siteplan` and `api` execute, then restore.
            snapshot = {name: sys.modules.get(name) for name in _FLAT_STAGE}
            try:
                for name, module in flat.items():
                    sys.modules[name] = module
                extra = _exec_flat(_FLAT_API[len(_FLAT_STAGE) :])
            finally:
                for name, previous in snapshot.items():
                    if previous is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = previous
            _API = {**flat, **extra}
        return _API


def _loaded_rules(modules: dict[str, ModuleType]) -> Any:
    """The top-level `rules.load_rules()` result, cached per process (rules.yaml is
    committed and does not change within a process)."""

    global _RULES
    if _RULES is None:
        _RULES = modules["rules"].load_rules()
    return _RULES


_SOURCE_SCENE = "scene/scene.json"
_OBJECTS_3D = "objects/objects_3d.json"


def _read_input(ctx: StageContext, key: str) -> bytes:
    """The hydrated run artifact's bytes. Missing means refusal, never a fallback."""

    path = Path(ctx.work_dir) / key
    if not path.is_file():
        raise PlacementInputError(
            f"{key} is missing from this run's artifacts: placement only solves the "
            "scene this capture produced (STAGE_INPUTS['placement'] hydrates it)"
        )
    return path.read_bytes()


def _objects_3d_evidence(ctx: StageContext) -> None:
    """The objects_3d readiness gate: stage done, artifact the declared format.

    Readiness evidence only — the coordinator's `_merge_if_ready` has already merged
    the detected objects into the source scene, and this stage never merges them again.
    """

    record = ctx.state.get_stage(ctx.run_id, "objects_3d")
    status = getattr(record, "status", None)
    if status != "done":
        raise PlacementObjectsError(
            f"objects_3d stage status is {status or 'missing'!r}, not done: the scene's "
            "hazards may be incomplete, so placement refuses rather than implying no hazards"
        )
    raw = _read_input(ctx, _OBJECTS_3D)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PlacementObjectsError(f"{_OBJECTS_3D} is not readable JSON: {exc}") from exc
    fmt = document.get("format") if isinstance(document, dict) else None
    if fmt != "hss-objects-3d/v0":
        raise PlacementObjectsError(f'{_OBJECTS_3D} must carry format "hss-objects-3d/v0", got {fmt!r}')


def _source_scene_bytes(ctx: StageContext) -> bytes:
    """The merged source scene's bytes, JSON-parsed and re-serialized deterministically
    after conversion — those converted bytes are the solver's whole world."""

    raw = _read_input(ctx, _SOURCE_SCENE)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PlacementSceneError(f"source scene {_SOURCE_SCENE} is not readable JSON: {exc}") from exc
    # Validates the merged Scene (duplicate ids, generated or unsupported evidence,
    # self-contradictory geometry) and converts to the wire scene. SceneBridgeError
    # carries its own typed code and JSON pointer; it is a ValueError, so it must not
    # be caught by the solver's ValueError handler below.
    converted = convert_scene(document)
    # Sorted keys, no NaN/Inf: deterministic bytes, and a non-finite value fails here
    # instead of reaching the solver.
    return json.dumps(converted, sort_keys=True, allow_nan=False).encode("utf-8")


def run(ctx: StageContext) -> StageResult:
    """Orchestrator stage entry: convert this capture's merged scene, solve it,
    publish the artifact bytes verbatim."""

    modules = load_solver()
    scene_mod, solver_mod = modules["scene"], modules["solver"]
    _objects_3d_evidence(ctx)
    scene_bytes = _source_scene_bytes(ctx)
    try:
        rules = _loaded_rules(modules)
        parsed = scene_mod.parse_scene(json.loads(scene_bytes.decode("utf-8")), rules.rules, scene_bytes)
        result = solver_mod.solve(parsed, rules)
        text = json.dumps(result, sort_keys=True, allow_nan=False)
    except scene_mod.SceneError as exc:  # schema and geometry refusals from the parser
        raise PlacementSceneError(str(exc)) from exc
    except ValueError as exc:  # SceneTooComplex, non-finite lengths
        raise PlacementSceneError(str(exc)) from exc

    out_dir = Path(ctx.work_dir) / "placement"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "placement.json").write_bytes(text.encode("utf-8"))
    (out_dir / "scene.json").write_bytes(scene_bytes)
    return StageResult(
        artifacts={
            "placement/placement.json": "placement/placement.json",
            "placement/scene.json": "placement/scene.json",
        },
        metrics={"elapsed_ms": float(result["stats"]["elapsed_ms"])},
        warnings=[],
    )
