"""Modal entry. Importing this module does not require Modal; deploy does.

`modal deploy -m server.api.modal_entry` looks up `api_app`. The label is `capture-api` (SD-32).
"""

from __future__ import annotations

import os

api_app = None
app = None
ingest_sweep = None

try:
    import modal
except ImportError:  # the test suite and local uvicorn do not install Modal
    modal = None

if modal is not None:
    api_app = modal.App("hss-capture-api")
    app = api_app
    api_image = (
        modal.Image.debian_slim(python_version="3.12")
        .pip_install_from_pyproject("pyproject.toml", optional_dependencies=["api"])
        .add_local_python_source("server")
    )
    _env = os.environ.get("SMP_ENVIRONMENT", "dev")
    # hss-dev-gcp is attached once the GCP project exists. Volume deploys only need the API secret.
    _secrets = [modal.Secret.from_name(f"hss-{_env}-api")]
    _volumes = {"/vol": modal.Volume.from_name("house-capture-pipeline", create_if_missing=True)}

    @api_app.function(
        image=api_image,
        secrets=_secrets,
        volumes=_volumes,
        min_containers=int(os.environ.get("HSS_API_MIN_CONTAINERS", "1")),
        max_containers=1,
        scaledown_window=300,
        timeout=120,
    )
    @modal.concurrent(max_inputs=200)
    @modal.asgi_app(label="capture-api")
    def capture_api():
        from server.api.app import create_app

        # Demo stack (SD-86): no bearer. Static tokens and JWT stay unused.
        os.environ["HSS_AUTH_MODE"] = "none"
        return create_app()

    @api_app.function(
        image=api_image, secrets=_secrets, volumes=_volumes, schedule=modal.Period(minutes=5), timeout=240
    )
    def ingest_sweep():
        from server.api.sweeper import ingest_sweep as sweep

        return sweep()
