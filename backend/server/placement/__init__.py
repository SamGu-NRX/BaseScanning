"""The real CPU placement stage: the top-level server's solver, orchestrated.

`POST /v1/placements` in the top-level `server/` directory already solves a wire-format
scene against the public rules and returns a schema-valid decision. This package runs
that unchanged solver code as an orchestrator stage between `scene` and `criteria`:

- `server.placement.adapter` loads the top-level flat modules by explicit absolute file
  path (they are not a package, and bare names like `scene` collide with the eval vendor
  tree), so no import in this process can accidentally resolve `backend.server` or the
  vendor copy.
- The stage copies the solver's result verbatim to `placement/placement.json` and the
  exact input bytes to `placement/scene.json`.
- A malformed or unplaceable scene raises `PlacementSceneError` (code
  `placement_scene_invalid`); the stage is optional, so the run degrades
  (`degraded:placement`, capture `manual_review`) instead of failing. Modal images ship
  only the backend package (`add_local_python_source("server")`), so containers without
  the repo-root `server/` tree degrade the same way through `PlacementBoundaryError`.
"""

from server.placement.adapter import (
    PlacementBoundaryError,
    PlacementSceneError,
    load_solver,
    run,
)

__all__ = ["PlacementBoundaryError", "PlacementSceneError", "load_solver", "run"]
