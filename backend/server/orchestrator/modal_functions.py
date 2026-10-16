"""Modal functions for this workstream, defined on WS-12's ``app``.

Importing this module does not contact Modal and does not require a token. When ``modal``
or ``server.modal_app`` is missing, the deployed names stay unset and callers report that.
This module never constructs its own ``modal.App``.

The callable bodies always run the coordinator. Deployed wrappers add WS-12's
``pipeline_*`` knobs and ``secrets_for`` when that image is present.
"""

from __future__ import annotations

import json
import os

from server.common.deploy_switch import real_runs_enabled, shared_remote_backends
from server.contracts.jobs import CaptureRecord
from server.orchestrator.api import start_run
from server.orchestrator.api import sweep as sweep_runs
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.executor import CoordinatorDied
from server.orchestrator.modal_spawn import spawn_on_deployed_app, sync_volume
from server.orchestrator.privacy import apply_deployed_privacy
from server.orchestrator.services import bind, current, packet_sha256

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


def use_shared_dev_backends() -> None:
    """Orchestrator containers share one Modal Dict. Prod keeps its own settings.

    In-process callers (unit tests) must not rewrite process env. ``Settings()``
    reads ``VOLUME_ROOT`` and would try to create ``/vol``.
    """

    if not os.environ.get("MODAL_TASK_ID"):
        return
    if os.environ.get("SMP_ENVIRONMENT", "dev") == "prod":
        return
    if real_runs_enabled():
        # Same dict and GCS bucket capture-api baked. Do not point storage at /vol.
        os.environ.update(shared_remote_backends(os.environ.get("SMP_ENVIRONMENT", "dev")))
        return
    os.environ["STATE_BACKEND"] = "modal_dict"
    os.environ.setdefault("STATE_DICT_NAME", "hss-dev-state")
    os.environ.setdefault("SMP_ENVIRONMENT", "dev")
    # Preserve GCS baked into workers even when the deploy-shell remote flag is absent.
    # Stage scratch is a local temp dir, so a commit does not upload the packet again.
    os.environ["VOLUME_ROOT"] = "/vol"
    if os.environ.get("STORAGE_BACKEND") != "gcs":
        os.environ["STORAGE_BACKEND"] = "local"


def coordinate_run_impl(run_id: str) -> str:
    """One container drives the run to a terminal status."""

    use_shared_dev_backends()
    apply_deployed_privacy()
    try:
        RunCoordinator(current()).drive(run_id)
    except CoordinatorDied:
        pass
    return run_id


def run_stage_impl(run_id: str, stage: str, attempt: int) -> dict[str, object]:
    """One pipeline container executes the stage the coordinator named."""

    use_shared_dev_backends()
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


def run_capture_impl(spec: dict[str, object]) -> dict[str, object]:
    """Create one capture in the shared dict and drive it. The caller is the coordinator."""

    use_shared_dev_backends()
    os.environ["HSS_ORCH_MODAL_REMOTE"] = "1"
    os.environ["HSS_ORCH_STAGE_STUBS"] = "1" if spec.get("stubs", True) else "0"
    os.environ["HSS_ORCH_TEST_HANG_STAGE"] = str(spec.get("hang_stage") or "")
    os.environ["HSS_ORCH_TEST_FAIL_STAGE"] = str(spec.get("fail_stage") or "")
    os.environ["HSS_ORCH_TEST_KILL_COORDINATOR_AFTER"] = str(spec.get("kill_coordinator_after") or "")
    if spec.get("hard_stop_min"):
        os.environ["HARD_STOP_MIN"] = str(spec["hard_stop_min"])
    apply_deployed_privacy()
    packet_text = spec.get("packet")
    if not isinstance(packet_text, str) or not packet_text:
        raise ValueError("packet json is required")
    data = packet_text.encode()
    packet = json.loads(data)
    capture_id = str(spec.get("capture_id") or packet.get("captureId") or "cap_modal")
    services = current()
    with bind(services):
        services.state.put_capture(  # type: ignore[attr-defined]
            CaptureRecord(
                capture_id=capture_id,
                packet_id=str(packet.get("packetId") or "packet-modal"),
                status="awaiting_files",
                tier="arkit",
                flow=str(packet.get("flow") or "guided"),  # type: ignore[arg-type]
                device_model="modal",
                owner_principal="modal",
                created_at=services.clock.iso(),
                finalize_by=services.clock.iso(),
            )
        )
        uri = f"captures/{capture_id}/packet.json"
        services.storage.put_bytes(uri, data, "application/json")  # type: ignore[attr-defined]
        sync_volume("commit")
        run = start_run(capture_id, packet_sha256(data), uri)
    return {
        "capture_id": capture_id,
        "run_id": run.run_id,
        "status": run.status,
        "failure_code": run.failure_code,
    }


APP_NAME = os.environ.get("MODAL_APP_NAME", "").strip() or "house-scanning"
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
        volumes={"/vol": volume} if volume is not None else {},
        **_cpu_secrets,
    )
    def coordinate_run(run_id: str) -> str:
        return coordinate_run_impl(run_id)

    @app.function(  # type: ignore[union-attr]
        image=CPU_IMAGE,
        cpu=1,
        timeout=180,
        retries=0,
        **_cpu_secrets,
    )
    def prove_app_spawn() -> dict[str, object]:
        """Spawn a no-op on this app. Sweeper secret, same bake as the coordinator."""

        return spawn_on_deployed_app("capture_check", "d142c-probe")

    @app.function(  # type: ignore[union-attr]
        image=CPU_IMAGE,
        cpu=2,
        timeout=420,
        retries=0,
        volumes={"/vol": volume} if volume is not None else {},
        **_cpu_secrets,
    )
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

    _sweep_kwargs: dict[str, object] = {
        "image": CPU_IMAGE,
        "cpu": 1,
        "timeout": SWEEPER_TIMEOUT_S,
        "retries": 0,
        "volumes": {"/vol": volume} if volume is not None else {},
        **_secret_kwargs("sweeper"),
    }
    # The shared app owns the five-minute sweep. A throwaway probe must not
    # schedule a second sweeper against the same dev dict.
    if os.environ.get("HSS_MODAL_SCHEDULE", "on") != "off":
        _sweep_kwargs["schedule"] = modal.Period(minutes=5)

    @app.function(**_sweep_kwargs)  # type: ignore[union-attr]
    def sweep() -> int:
        use_shared_dev_backends()
        apply_deployed_privacy()
        return sweep_runs()

    @app.function(  # type: ignore[union-attr]
        image=CPU_IMAGE,
        cpu=2,
        timeout=COORDINATOR_TIMEOUT_S,
        retries=0,
        volumes={"/vol": volume} if volume is not None else {},
        **_pipeline_secrets,
    )
    def run_capture(spec: dict) -> dict:
        return run_capture_impl(spec)

else:
    Pipeline = _Missing("Pipeline")  # type: ignore[misc,assignment]
    coordinate_run = _Missing("coordinate_run")  # type: ignore[misc,assignment]
    prove_app_spawn = _Missing("prove_app_spawn")  # type: ignore[misc,assignment]
    run_cpu_stage = _Missing("run_cpu_stage")  # type: ignore[misc,assignment]
    early_reads = _Missing("early_reads")  # type: ignore[misc,assignment]
    EarlyQa = _Missing("EarlyQa")  # type: ignore[misc,assignment]
    capture_check = _Missing("capture_check")  # type: ignore[misc,assignment]
    sweep = _Missing("sweep")  # type: ignore[misc,assignment]
    run_capture = _Missing("run_capture")  # type: ignore[misc,assignment]
