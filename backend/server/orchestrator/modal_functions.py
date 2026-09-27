"""Modal functions for this workstream, defined on WS-12's ``app``.

Importing this module does not contact Modal and does not require a token. When ``modal``
or ``server.modal_app`` is missing, the deployed names stay unset and callers report that.
This module never constructs its own ``modal.App``.

The callable bodies always run the coordinator. Deployed wrappers add WS-12's
``pipeline_*`` knobs and ``secrets_for`` when that image is present.
"""

from __future__ import annotations

from server.orchestrator.api import sweep as sweep_runs
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.privacy import apply_deployed_privacy
from server.orchestrator.services import current

try:
    import modal
except ImportError:
    modal = None  # type: ignore[assignment]

try:
    from server.modal_app import CPU_IMAGE, GPU_IMAGE, KNOBS, app, volume
except ImportError:
    app = None
    GPU_IMAGE = None
    CPU_IMAGE = None
    volume = None
    KNOBS = {}


class _Missing:
    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError(
            f"{self.name} needs the modal SDK and server.modal_app (WS-12). No token is used and no GPU is spawned."
        )

    def __getattr__(self, item: str) -> _Missing:
        return _Missing(f"{self.name}.{item}")


def modal_available() -> bool:
    return modal is not None and app is not None


def pipeline_resources(knobs: dict[str, object] | None = None) -> dict[str, object]:
    """WS-12 names. Older ``gpu`` / ``min_containers`` keys are not consulted."""

    source = KNOBS if knobs is None else knobs
    return {
        "gpu": source.get("pipeline_gpu", "L4"),
        "min_containers": int(source.get("pipeline_min_containers", 0)),
        "max_containers": int(source.get("pipeline_max_containers", 3)),
    }


def _secret_kwargs(service: str) -> dict[str, object]:
    try:
        from server.modal_app import secrets_for
    except ImportError:
        return {}
    return {"secrets": secrets_for(service)}


def coordinate_run_impl(run_id: str) -> str:
    """One container drives the run to a terminal status."""

    apply_deployed_privacy()
    RunCoordinator(current()).drive(run_id)
    return run_id


def run_stage_impl(run_id: str, stage: str, attempt: int) -> dict[str, object]:
    """One pipeline container executes the stage the coordinator named."""

    apply_deployed_privacy()
    resources = pipeline_resources()
    artifacts = RunCoordinator(current()).execute_named_stage(
        run_id,
        stage,
        attempt,
        gpu=str(resources["gpu"]),
    )
    return {
        "run_id": run_id,
        "stage": stage,
        "attempt": attempt,
        "gpu": resources["gpu"],
        "artifacts": artifacts,
    }


APP_NAME = "house-scanning"
PIPELINE_TIMEOUT_S = 1200
COORDINATOR_TIMEOUT_S = 1560
SWEEPER_TIMEOUT_S = 240
EARLY_RETRIES = {"max_retries": 2, "initial_delay": 2.0, "backoff_coefficient": 2.0}

if modal is not None and app is not None:
    _resources = pipeline_resources()
    _pipeline_secrets = _secret_kwargs("pipeline")
    _cpu_secrets = _secret_kwargs("sweeper")

    @app.cls(  # type: ignore[union-attr]
        image=GPU_IMAGE,
        gpu=_resources["gpu"],
        cpu=8,
        memory=24576,
        timeout=PIPELINE_TIMEOUT_S,
        scaledown_window=900,
        volumes={"/vol": volume} if volume is not None else {},
        min_containers=_resources["min_containers"],
        max_containers=_resources["max_containers"],
        **_pipeline_secrets,
    )
    class Pipeline:
        @modal.enter()
        def load(self) -> None:
            apply_deployed_privacy()

        @modal.method()
        def warm(self) -> dict[str, object]:
            return {"enter_s": 0.0, "model_load_s": 0.0, "gpu": pipeline_resources()["gpu"]}

        @modal.method()
        def run_stage(self, run_id: str, stage: str, attempt: int) -> dict[str, object]:
            return run_stage_impl(run_id, stage, attempt)

    @app.function(  # type: ignore[union-attr]
        image=CPU_IMAGE,
        cpu=2,
        timeout=COORDINATOR_TIMEOUT_S,
        retries=0,
        **_cpu_secrets,
    )
    def coordinate_run(run_id: str) -> str:
        return coordinate_run_impl(run_id)

    @app.function(image=CPU_IMAGE, cpu=2, timeout=420, retries=0, **_cpu_secrets)  # type: ignore[union-attr]
    def run_cpu_stage(run_id: str, stage: str, attempt: int) -> dict[str, object]:
        return run_stage_impl(run_id, stage, attempt)

    @app.function(  # type: ignore[union-attr]
        image=CPU_IMAGE,
        cpu=2,
        timeout=420,
        retries=modal.Retries(**EARLY_RETRIES),
        **_cpu_secrets,
    )
    def early_reads(capture_id: str, path: str) -> str:
        apply_deployed_privacy()
        return f"{capture_id}:{path}"

    @app.cls(  # type: ignore[union-attr]
        image=CPU_IMAGE,
        cpu=2,
        scaledown_window=600,
        timeout=240,
        **_cpu_secrets,
    )
    class EarlyQa:
        @modal.enter()
        def load(self) -> None:
            apply_deployed_privacy()

        @modal.method()
        def warm(self) -> dict[str, float]:
            return {"enter_s": 0.0}

    @app.function(  # type: ignore[union-attr]
        image=CPU_IMAGE,
        cpu=1,
        timeout=60,
        retries=modal.Retries(**EARLY_RETRIES),
        **_cpu_secrets,
    )
    def capture_check(capture_id: str) -> str:
        apply_deployed_privacy()
        return capture_id

    @app.function(  # type: ignore[union-attr]
        image=CPU_IMAGE,
        cpu=1,
        timeout=SWEEPER_TIMEOUT_S,
        retries=0,
        schedule=modal.Period(minutes=5),
        **_secret_kwargs("sweeper"),
    )
    def sweep() -> int:
        apply_deployed_privacy()
        return sweep_runs()

else:
    Pipeline = _Missing("Pipeline")  # type: ignore[misc,assignment]
    coordinate_run = _Missing("coordinate_run")  # type: ignore[misc,assignment]
    run_cpu_stage = _Missing("run_cpu_stage")  # type: ignore[misc,assignment]
    early_reads = _Missing("early_reads")  # type: ignore[misc,assignment]
    EarlyQa = _Missing("EarlyQa")  # type: ignore[misc,assignment]
    capture_check = _Missing("capture_check")  # type: ignore[misc,assignment]
    sweep = _Missing("sweep")  # type: ignore[misc,assignment]
