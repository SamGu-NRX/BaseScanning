"""Real-run deploy switch. No Modal import, so finalize and the replay client can read it.

Dev keeps stubs on unless the deploy shell sets both flags. Prod omits the stubs
key and leaves the Settings default. See docs/INTEGRATION.md "Switch for real runs".
"""

from __future__ import annotations

import os

CAPTURE_API_TIMEOUT_S = 600
CAPTURE_API_REAL_RUN_TIMEOUT_S = 1560
REAL_RUN_CLIENT_WAIT_S = 1500
STUB_FINALIZE_ETA_S = 180
AWAITING_FILES_ETA_S = 200


def _flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def real_runs_enabled(environ: dict[str, str] | None = None) -> bool:
    """Deploy-shell switch. Unset leaves capture-api inline with the short timeout."""
    env = os.environ if environ is None else environ
    return _flag(env.get("HSS_ORCH_MODAL_REMOTE", "0"))


def web_function_timeout_s(environ: dict[str, str] | None = None) -> int:
    if real_runs_enabled(environ):
        return CAPTURE_API_REAL_RUN_TIMEOUT_S
    return CAPTURE_API_TIMEOUT_S


def baked_stage_stubs(environment: str, environ: dict[str, str] | None = None) -> str | None:
    """Stubs value to bake, or None when the key should be omitted.

    Dev writes ``0`` only when remote spawning is on and the shell set
    ``HSS_ORCH_STAGE_STUBS=0``. Every other dev deploy writes ``1``.
    """
    if environment != "dev":
        return None
    env = os.environ if environ is None else environ
    if real_runs_enabled(env) and env.get("HSS_ORCH_STAGE_STUBS", "").strip() == "0":
        return "0"
    return "1"


def modal_app_name(environ: dict[str, str] | None = None) -> str:
    """Deployed Modal app. A throwaway probe sets ``MODAL_APP_NAME`` so it does not call house-scanning."""
    env = os.environ if environ is None else environ
    name = env.get("MODAL_APP_NAME", "").strip()
    return name or "house-scanning"


def optional_runtime_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Copy probe knobs into the function env only when the deploy shell set them.

    Empty on a normal deploy. ``HSS_ORCH_PREWARM=0`` skips the GPU warm.
    ``HSS_ORCH_TEST_KILL_COORDINATOR_AFTER`` stops the coordinator after that stage spawns.
    """
    env = os.environ if environ is None else environ
    fields: dict[str, str] = {}
    for key in ("HSS_ORCH_PREWARM", "HSS_ORCH_TEST_KILL_COORDINATOR_AFTER"):
        value = env.get(key, "").strip()
        if value:
            fields[key] = value
    return fields


def shared_state_env(environment: str, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Modal Dict name every capture-api container shares (SD-72)."""
    env = os.environ if environ is None else environ
    name = env.get("STATE_DICT_NAME", "").strip() or f"hss-{environment}-state"
    return {"STATE_BACKEND": "modal_dict", "STATE_DICT_NAME": name}


def shared_remote_backends(environment: str, environ: dict[str, str] | None = None) -> dict[str, str]:
    """State and storage for capture-api, pipeline, and sweeper when remote spawn is on.

    Empty when the switch is off. The packet lives in GCS, so workers must not
    fall back to a private ``/vol`` store.
    """
    env = os.environ if environ is None else environ
    if not real_runs_enabled(env):
        return {}
    fields = shared_state_env(environment, env)
    fields["STORAGE_BACKEND"] = "gcs"
    return fields


def finalize_eta_s(status: str, environ: dict[str, str] | None = None) -> int:
    if status == "awaiting_files":
        return AWAITING_FILES_ETA_S
    if real_runs_enabled(environ):
        return REAL_RUN_CLIENT_WAIT_S
    return STUB_FINALIZE_ETA_S
