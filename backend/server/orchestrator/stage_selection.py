"""Per-stage real-vs-stand-in selection on the dev stub path.

``HSS_ORCH_STAGE_STUBS=1`` is all or nothing: every stage except the
``stubs_exempt`` placement stage runs ``server/orchestrator/stubs/<stage>.py``,
so the answers a local run produces never depend on the scan. This module adds
the middle setting: ``HSS_ORCH_REAL_STAGES`` names the stages whose *real*
implementations run even with stubs on, per stage, so a local run can exercise
one real stage at a time against stand-in upstreams.

Grammar: a comma-separated list of stage names, or the alias ``cpu``, which
selects every stage whose real path needs no GPU, no model weights and no paid
call. Two stages stay out of ``cpu``:

- ``objects`` loads OWLv2/SigLIP weights (``server/objects/run.py``), so
  listing it needs ``HSS_OBJECTS_DEVICE=cpu`` as well.
- ``dense`` falls back to π³/MoGe/DA3 weights on a CUDA device whenever the
  packet carries no LiDAR and no points were injected
  (``server/dense/run.py`` ``_acquire_cloud``). It can still be listed by name.

``reads`` and ``criteria`` call a paid vision model when
``VISION_EXTERNAL_CALLS`` is on, so ``cpu`` is refused in that case.

The variable is dev-only by construction: it is refused together with a true
``HSS_ORCH_MODAL_REMOTE`` (a remote run already runs everything real) and
unless ``SMP_ENVIRONMENT`` is exactly ``dev``. Refusals raise as early as the
process can see them — :mod:`server.orchestrator.runner` validates at import,
which is the startup of every process that can run a stage.

This module reads the environment itself and does not extend
``server/orchestrator/config.py``: that file is another packet's file this
wave. It does reuse that file's boolean parser, so a value means the same thing
in both places.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from server.orchestrator.config import _flag
from server.orchestrator.graph import STAGE_NAMES

ENV_VAR = "HSS_ORCH_REAL_STAGES"
CPU_ALIAS = "cpu"
REMOTE_VAR = "HSS_ORCH_MODAL_REMOTE"
ENVIRONMENT_VAR = "SMP_ENVIRONMENT"
OBJECTS_DEVICE_VAR = "HSS_OBJECTS_DEVICE"
VISION_VAR = "VISION_EXTERNAL_CALLS"

#: Stages whose real path is deterministic CPU work: no GPU, no local weights,
#: no paid or networked model call (the last holds while VISION_EXTERNAL_CALLS
#: is off, which the parser enforces for the alias). ``objects`` and ``dense``
#: are the deliberate omissions; see the module docstring.
CPU_STAGES: tuple[str, ...] = (
    "validate",
    "poses",
    "reads",
    "scale",
    "coverage",
    "scene",
    "objects_3d",
    "placement",
    "criteria",
    "result",
)

# Cache key: the env vars the parse reads. A cache entry survives only while
# they are unchanged, so tests can monkeypatch the environment freely.
_CACHE_KEYS = (ENV_VAR, REMOTE_VAR, ENVIRONMENT_VAR, OBJECTS_DEVICE_VAR, VISION_VAR)
_cache: tuple[tuple[tuple[str, str], ...], frozenset[str]] | None = None


def parse_real_stages(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """Parse and validate ``HSS_ORCH_REAL_STAGES``. Raises ``ValueError`` on refusals."""

    env = os.environ if environ is None else environ
    raw = (env.get(ENV_VAR) or "").strip()
    if not raw:
        return frozenset()
    remote = env.get(REMOTE_VAR, "0")
    # load_orch parses this variable with _flag, so every spelling it treats as
    # true (1, true, yes, on, any case) is refused here too.
    if _flag(remote):
        raise ValueError(
            f"{ENV_VAR}={raw!r} selects real stages for a local run, but {REMOTE_VAR}={remote!r} already runs "
            "every stage for real; set one or the other, never both."
        )
    # Fail closed, matching Settings.SMP_ENVIRONMENT (server/common/config.py):
    # a missing, blank or unknown value is prod there, so only an exact "dev" is
    # dev here. An unconfigured production process therefore refuses a selection.
    environment = env.get(ENVIRONMENT_VAR)
    if environment != "dev":
        shown = "unset" if environment is None else f"{environment!r}"
        raise ValueError(
            f"{ENV_VAR}={raw!r} is a dev-only selection, but {ENVIRONMENT_VAR} is {shown}, which Settings "
            f"reads as prod. Set {ENVIRONMENT_VAR}=dev exactly, or unset {ENV_VAR}."
        )
    known = frozenset(STAGE_NAMES)
    selected: set[str] = set()
    for part in raw.split(","):
        name = part.strip()
        if not name:
            continue
        if name == CPU_ALIAS:
            vision = env.get(VISION_VAR, "")
            # reads switches on for "on"; criteria also for 1/true/yes. _flag
            # covers both sets, so the alias never pulls in a paid call.
            if _flag(vision):
                raise ValueError(
                    f"{ENV_VAR} uses the alias {CPU_ALIAS!r}, which promises no paid call, but "
                    f"{VISION_VAR}={vision!r} lets reads and criteria call a vision model. Set "
                    f"{VISION_VAR}=off, or list the stages by name."
                )
            selected.update(CPU_STAGES)
            continue
        if name not in known:
            raise ValueError(
                f"{ENV_VAR} names unknown stage {name!r} (full value {raw!r}); known stages are "
                f"{', '.join(STAGE_NAMES)} plus the alias {CPU_ALIAS!r}."
            )
        if name == "objects" and (env.get(OBJECTS_DEVICE_VAR) or "").strip().lower() != "cpu":
            raise ValueError(
                f"{ENV_VAR} lists objects, whose real path loads OWLv2/SigLIP weights "
                "(server/objects/run.py); it is refused unless "
                f"{OBJECTS_DEVICE_VAR}=cpu is also set to run them locally, or drop objects from {ENV_VAR}."
            )
        selected.add(name)
    return frozenset(selected)


def real_stages() -> frozenset[str]:
    """The validated selection for the current environment, cached per process."""

    global _cache
    env = os.environ
    key = tuple((name, env.get(name, "")) for name in _CACHE_KEYS)
    if _cache is None or _cache[0] != key:
        _cache = (key, parse_real_stages(env))
    return _cache[1]


def is_real(stage: str) -> bool:
    """True when the stage's real implementation is selected for this process."""

    return stage in real_stages()
