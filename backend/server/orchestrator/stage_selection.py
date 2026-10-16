"""Per-stage real-vs-stand-in selection on the dev stub path.

``HSS_ORCH_STAGE_STUBS=1`` is all or nothing: every stage except the
``stubs_exempt`` placement stage runs ``server/orchestrator/stubs/<stage>.py``,
so the answers a local run produces never depend on the scan. This module adds
the middle setting: ``HSS_ORCH_REAL_STAGES`` names the stages whose *real*
implementations run even with stubs on, per stage, so a local run can exercise
one real stage at a time against stand-in upstreams.

Grammar: a comma-separated list of stage names, or the alias ``cpu``, which
selects every stage whose real path needs no GPU, no model weights and no paid
call. ``objects`` is the one stage ``cpu`` must not pull in: its real path loads
OWLv2/SigLIP weights (``server/objects/run.py``), so listing it needs
``HSS_OBJECTS_DEVICE=cpu`` as well.

The variable is dev-only by construction: it is refused together with
``HSS_ORCH_MODAL_REMOTE=1`` (a remote run already runs everything real) and
outside ``SMP_ENVIRONMENT=dev``. Refusals raise as early as the process can see
them — :mod:`server.orchestrator.runner` validates at import, which is the
startup of every process that can run a stage.

This module reads the environment itself and does not extend
``server/orchestrator/config.py``: that file is another packet's file this
wave.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from server.orchestrator.graph import STAGE_NAMES

ENV_VAR = "HSS_ORCH_REAL_STAGES"
CPU_ALIAS = "cpu"
REMOTE_VAR = "HSS_ORCH_MODAL_REMOTE"
ENVIRONMENT_VAR = "SMP_ENVIRONMENT"
OBJECTS_DEVICE_VAR = "HSS_OBJECTS_DEVICE"

#: Stages whose real path is deterministic CPU work: no GPU, no local weights,
#: no paid or networked model call. ``objects`` is the deliberate omission —
#: its real path loads OWLv2/SigLIP weights, so it never rides the alias.
CPU_STAGES: tuple[str, ...] = (
    "validate",
    "poses",
    "reads",
    "scale",
    "dense",
    "coverage",
    "scene",
    "objects_3d",
    "placement",
    "criteria",
    "result",
)

# Cache key: the env vars the parse reads. A cache entry survives only while
# they are unchanged, so tests can monkeypatch the environment freely.
_CACHE_KEYS = (ENV_VAR, REMOTE_VAR, ENVIRONMENT_VAR, OBJECTS_DEVICE_VAR)
_cache: tuple[tuple[tuple[str, str], ...], frozenset[str]] | None = None


def parse_real_stages(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """Parse and validate ``HSS_ORCH_REAL_STAGES``. Raises ``ValueError`` on refusals."""

    env = os.environ if environ is None else environ
    raw = (env.get(ENV_VAR) or "").strip()
    if not raw:
        return frozenset()
    if (env.get(REMOTE_VAR) or "").strip() == "1":
        raise ValueError(
            f"{ENV_VAR}={raw!r} selects real stages for a local run, but {REMOTE_VAR}=1 already runs "
            "every stage for real; set one or the other, never both."
        )
    environment = (env.get(ENVIRONMENT_VAR) or "dev").strip() or "dev"
    if environment != "dev":
        raise ValueError(
            f"{ENV_VAR}={raw!r} is a dev-only selection; {ENVIRONMENT_VAR}={environment!r} runs the real "
            "orchestration, where the variable has nothing to select. Unset one of the two."
        )
    known = frozenset(STAGE_NAMES)
    selected: set[str] = set()
    for part in raw.split(","):
        name = part.strip()
        if not name:
            continue
        if name == CPU_ALIAS:
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
