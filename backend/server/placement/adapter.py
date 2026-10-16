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
- Input: `placement/scene.json` in the run artifacts — a wire-format scene, the same
  bytes `POST /v1/placements` accepts. Nothing publishes that key yet (the bridge from
  Hunter's scene output is future work), so the stage falls back to the committed
  reference fixture `reference_scene.json`, a byte-identical copy of the top-level
  server's `tests/fixtures/example-scene.json`; the fallback is recorded as a stage
  warning.
- Output: `placement/placement.json` — the solver's result verbatim (sorted keys), valid
  against the top-level `schemas/result.schema.json`; `placement/scene.json` — the
  exact input bytes. Only `stats.elapsed_ms` varies between runs: the decision is
  deterministic for fixed scene bytes and rules.
- Failure: a malformed or unplaceable scene raises `PlacementSceneError` (code
  `placement_scene_invalid`, not retryable — the bytes do not heal on a retry). A
  missing solver tree raises `PlacementBoundaryError` (code `placement_boundary_missing`).
  Both are recorded stage data; the optional stage degrades the run instead of failing
  it.
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


def _scene_input(ctx: StageContext) -> tuple[bytes, bool]:
    """The scene bytes and whether they came from the run artifact (as opposed to the
    committed reference fixture)."""

    # stage_context hydrates the stage's selected run artifacts as files under the
    # work directory (STAGE_INPUTS["placement"] admits "placement/scene.json");
    # ctx.inputs itself only carries packet_dir and run_control.
    artifact = Path(ctx.work_dir) / "placement" / "scene.json"
    if artifact.is_file():
        return artifact.read_bytes(), True
    fixture = Path(__file__).resolve().parent / "reference_scene.json"
    return fixture.read_bytes(), False


def run(ctx: StageContext) -> StageResult:
    """Orchestrator stage entry: solve the scene, publish the artifact bytes verbatim."""

    modules = load_solver()
    scene_mod, solver_mod = modules["scene"], modules["solver"]
    raw, from_artifact = _scene_input(ctx)
    try:
        document = json.loads(raw.decode("utf-8"))
        rules = _loaded_rules(modules)
        parsed = scene_mod.parse_scene(document, rules.rules, raw)
        result = solver_mod.solve(parsed, rules)
        text = json.dumps(result, sort_keys=True, allow_nan=False)
    except scene_mod.SceneError as exc:  # schema and geometry refusals from the parser
        raise PlacementSceneError(str(exc)) from exc
    except ValueError as exc:  # malformed JSON, SceneTooComplex, non-finite lengths
        raise PlacementSceneError(str(exc)) from exc

    out_dir = Path(ctx.work_dir) / "placement"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "placement.json").write_bytes(text.encode("utf-8"))
    (out_dir / "scene.json").write_bytes(raw)
    return StageResult(
        artifacts={
            "placement/placement.json": "placement/placement.json",
            "placement/scene.json": "placement/scene.json",
        },
        metrics={"elapsed_ms": float(result["stats"]["elapsed_ms"])},
        warnings=[]
        if from_artifact
        else [
            "scene_input: committed reference fixture (no placement/scene.json run "
            "artifact published yet; the bridge from Hunter's scene output is future work)"
        ],
    )
