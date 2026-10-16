"""Modal entry. Importing this module does not require Modal; deploy does.

Included by server.modal_app into the house-scanning app. Deploy that app, not
this module:

    SMP_ENVIRONMENT=dev GIT_SHA=$(git rev-parse HEAD) modal deploy -m server.modal_app -e dev

The web label stays `capture-api` (SD-32). The function image is the light CPU
image. Object storage is GCS. Do not import server.modal_app from here.
"""

from __future__ import annotations

import os

from server.common.deploy_switch import (
    baked_stage_stubs,
    modal_app_name,
    optional_runtime_env,
    real_runs_enabled,
    shared_state_env,
    web_function_timeout_s,
)

api_app = None
app = None
ingest_sweep = None
capture_api = None
gcs_probe = None

# Inline stub runs. Real runs raise this with the remote switch; see
# docs/INTEGRATION.md "Switch for real runs". 1560s matches coordinate_run
# and outlives the 20-minute hard stop while remote stages run.
# More than one container so a long inline run cannot be the only input slot.
CAPTURE_API_MAX_CONTAINERS = 4
CAPTURE_API_BUFFER_CONTAINERS = 1
CAPTURE_API_MAX_INPUTS = 32
# One in-flight input is enough to scale. A higher target packs the poll onto the container
# that is already running the inline pipeline.
CAPTURE_API_TARGET_INPUTS = 1


def capture_runtime_env(env: str, git_sha: str) -> dict[str, str]:
    """Env baked into the capture-api function. Keep equal to deploy_env("capture-api")."""
    fields = {
        "SMP_PRODUCT_ID": "house-scanning",
        "SMP_ENVIRONMENT": env,
        "SMP_SERVICE_NAME": "capture-api",
        "GIT_SHA": git_sha,
        "STORAGE_BACKEND": "gcs",
        # Orchestrator work dir. GcsStorage has no filesystem root; without this,
        # start_run cannot build a Services template on the GCS backend.
        "VOLUME_ROOT": "/vol",
    }
    if env == "dev":
        fields["HSS_INGEST_HOOKS"] = "auto"
        # SD-86. Auth off for the demo stack. Static tokens and JWT stay unused.
        fields["HSS_AUTH_MODE"] = "none"
    stubs = baked_stage_stubs(env)
    if stubs is not None:
        fields["HSS_ORCH_STAGE_STUBS"] = stubs
    # Scale-out reads the same run the container that accepted finalize wrote.
    fields.update(shared_state_env(env))
    fields["MODAL_APP_NAME"] = modal_app_name()
    fields.update(optional_runtime_env())
    if real_runs_enabled():
        # Coordinator stays here and spawns remote stages. Objects placement is WS-07.
        fields["HSS_ORCH_MODAL_REMOTE"] = "1"
    return fields


def apply_capture_env() -> None:
    """Write the dev capture env onto the process before Settings loads."""
    os.environ.update(
        capture_runtime_env(
            os.environ.get("SMP_ENVIRONMENT", "dev"),
            os.environ.get("GIT_SHA", "unknown"),
        )
    )


def capture_secret_names(env: str) -> list[str]:
    # hss-<env>-vlm is not created yet. Attaching a missing Modal secret fails deploy.
    return [f"hss-{env}-gcp", f"hss-{env}-api"]


try:
    import modal

    from server.modal_images import CPU_IMAGE
except ImportError:  # the test suite and local uvicorn do not install Modal
    modal = None
    CPU_IMAGE = None

if modal is not None:
    # Included into house-scanning. This name is not deployed on its own.
    # CPU_IMAGE is the light image. It does not build the COLMAP Dockerfile.
    api_app = modal.App("hss-capture-api")
    app = api_app
    api_image = CPU_IMAGE
    _env = os.environ.get("SMP_ENVIRONMENT", "dev")
    # from_dict is last so STORAGE_BACKEND=gcs overrides a volume value in hss-dev-api.
    _secrets = [modal.Secret.from_name(name) for name in capture_secret_names(_env)] + [
        modal.Secret.from_dict(capture_runtime_env(_env, os.environ.get("GIT_SHA", "unknown")))
    ]
    _volumes = {"/vol": modal.Volume.from_name("house-capture-pipeline", create_if_missing=True)}

    _min_containers = int(os.environ.get("HSS_API_MIN_CONTAINERS", "1"))
    _max_containers = int(os.environ.get("HSS_API_MAX_CONTAINERS", str(CAPTURE_API_MAX_CONTAINERS)))
    if _max_containers < _min_containers:
        _max_containers = _min_containers

    @api_app.function(
        image=api_image,
        secrets=_secrets,
        volumes=_volumes,
        min_containers=_min_containers,
        max_containers=_max_containers,
        buffer_containers=CAPTURE_API_BUFFER_CONTAINERS,
        scaledown_window=300,
        timeout=web_function_timeout_s(),
    )
    # Replaces allow_concurrent_inputs. A poll must run while another input is in flight.
    @modal.concurrent(max_inputs=CAPTURE_API_MAX_INPUTS, target_inputs=CAPTURE_API_TARGET_INPUTS)
    # SD-32. The shared app keeps this label. A throwaway deploy sets another so
    # it does not take the webhook the live house-scanning app already owns.
    @modal.asgi_app(label=os.environ.get("HSS_CAPTURE_API_LABEL", "capture-api"))
    def capture_api():
        apply_capture_env()
        from server.api.app import create_app

        return create_app()

    _ingest_kwargs: dict[str, object] = {
        "image": api_image,
        "secrets": _secrets,
        "volumes": _volumes,
        "timeout": 240,
    }
    if os.environ.get("HSS_MODAL_SCHEDULE", "on") != "off":
        _ingest_kwargs["schedule"] = modal.Period(minutes=5)

    @api_app.function(**_ingest_kwargs)
    def ingest_sweep():
        apply_capture_env()
        from server.api.sweeper import ingest_sweep as sweep

        return sweep()

    @api_app.function(image=api_image, secrets=_secrets, timeout=180)
    def gcs_probe() -> dict:
        """Write and delete one object in the capture bucket. No secret values."""
        from server.common.config import load, resolve_storage

        settings = load()
        storage = resolve_storage(settings)
        key = "work/_cutover-probe/ok.txt"
        storage.put_bytes(key, b"ok", "text/plain")
        got = storage.get_bytes(key)
        storage.delete(key)
        return {
            "ok": got == b"ok",
            "backend": settings.STORAGE_BACKEND,
            "bucket": settings.GCS_BUCKET,
            "env": settings.SMP_ENVIRONMENT,
            "hooks": settings.HSS_INGEST_HOOKS,
            "stubs": os.environ.get("HSS_ORCH_STAGE_STUBS", ""),
        }
