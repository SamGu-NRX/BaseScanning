"""Process-wide orchestrator services. Tests bind their own clock, state and executor."""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from server.common.backends.storage_local import LocalFsStorage
from server.common.config import Settings, load, resolve_state, resolve_storage
from server.common.fixture_meter import fixture_models_from_extras
from server.contracts.jobs import RunRecord, StageContext, StageRecord, run_id_for_capture
from server.orchestrator.artifacts import (
    ReadThrough,
    hydrate_artifacts,
    hydrate_packet,
    overlay_stage_artifacts,
    packet_cache_dir,
)
from server.orchestrator.clock import Clock
from server.orchestrator.config import OrchConfig, load_orch
from server.orchestrator.executor import InlineExecutor
from server.orchestrator.graph import select_stage_artifacts, stage_needs_packet_files
from server.orchestrator.spend import vlm_budget_usd

_CURRENT: contextvars.ContextVar[Services | None] = contextvars.ContextVar("hss_orch_services", default=None)


@dataclass
class Services:
    settings: Settings
    orch: OrchConfig
    state: object
    storage: object
    clock: Clock
    executor: InlineExecutor
    work_root: Path

    def stage_context(self, run: RunRecord, rec: StageRecord, call_id: str) -> StageContext:
        run = overlay_stage_artifacts(self.state, run)
        work = self.work_root / run.run_id / rec.stage / f"attempt-{rec.attempt}"
        work.mkdir(parents=True, exist_ok=True)
        packet_dir = work / "packet"
        packet_dir.mkdir(exist_ok=True)
        reads = self.reads_for(run.capture_id)
        packet_uri = getattr(run, "packet_uri", None) or f"captures/{run.capture_id}/packet.json"
        hydrate_packet(
            reads,
            self.state,
            run.capture_id,
            packet_dir,
            include_files=stage_needs_packet_files(rec.stage),
            cache_dir=packet_cache_dir(run.capture_id, run.packet_sha256),
            packet_key=packet_uri,
        )
        # Artifacts stay on raw storage. Scene merge replaces scene.json in place
        # after objects_3d has already read it, and a cache would keep the pre-merge bytes.
        hydrate_artifacts(self.storage, work, select_stage_artifacts(rec.stage, run.artifacts))
        spent = sum(float(run.spend_usd.get(key, 0.0)) for key in ("vlm_reads", "vlm_objects", "vlm_c3"))
        inputs = {"packet_dir": str(packet_dir), "run_control": str(work / "run_control.json")}
        (work / "run_control.json").write_text(
            json.dumps({"run": run.model_dump(mode="json"), "stage": rec.stage, "call_id": call_id})
        )
        if run.parent_run_id:
            parent = self.state.get_run(run.parent_run_id)  # type: ignore[attr-defined]
            if parent is not None:
                parent = overlay_stage_artifacts(self.state, parent)
                copied = hydrate_artifacts(
                    self.storage, work / "parent", select_stage_artifacts(rec.stage, parent.artifacts)
                )
                for key, path in copied.items():
                    inputs[f"parent/{key}"] = path
        return StageContext(
            capture_id=run.capture_id,
            run_id=run.run_id,
            stage=rec.stage,
            attempt=rec.attempt,
            branch=run.branch,
            flow=run.flow,
            tier=run.tier,
            work_dir=work,
            inputs=inputs,
            deadline_at=run.deadline_at,
            heartbeat=lambda _substep: None,
            gpu=rec.gpu,
            vlm_budget_usd=vlm_budget_usd(spent, self.orch),
            parent_run_id=run.parent_run_id,
            models=_coverage_models(self, run.capture_id, rec.stage),
            storage=self.storage,
            state=self.state,
            settings=self.settings,
        )

    def reads_for(self, capture_id: str) -> ReadThrough:
        """Reuse the packet and committed files across the stages of one capture.

        The capture-api runs stages inline, and each stage used to download the
        packet again. Published artifacts are not cached: a later writer can
        replace the same key, as scene merge does.
        """

        slot = getattr(self, "_reads_slot", None)
        if slot is None or slot[0] != capture_id or slot[1].storage is not self.storage:
            slot = (capture_id, ReadThrough(self.storage))
            self._reads_slot = slot
        return slot[1]


def _coverage_models(services: Services, capture_id: str, stage: str) -> dict | None:
    """Copy a dev-only capture override onto coverage. Other stages stay ``models=None``."""
    if stage != "coverage":
        return None
    getter = getattr(services.state, "get_kv", None)
    extra = getter(f"capturex:{capture_id}") if callable(getter) else None
    if not isinstance(extra, dict):
        extra = None
    return fixture_models_from_extras(extra, environment=services.settings.SMP_ENVIRONMENT)


def current() -> Services:
    bound = _CURRENT.get()
    if bound is not None:
        return bound
    settings = load()
    orch = load_orch()
    # GCS is the packet store. VOLUME_ROOT must not hide it behind a private directory.
    if settings.STORAGE_BACKEND == "gcs":
        storage = resolve_storage(settings)
    elif settings.VOLUME_ROOT:
        storage = LocalFsStorage(Path(settings.VOLUME_ROOT))
    else:
        storage = resolve_storage(settings)
    # Stage scratch stays off the volume. A remote worker hydrates from storage
    # into this directory, then publishes the artifacts back. Commit then uploads
    # only those artifacts, not a second copy of the packet.
    return Services(
        settings=settings,
        orch=orch,
        state=resolve_state(settings),
        storage=storage,
        clock=Clock(),
        executor=InlineExecutor(config=orch, stubs=orch.stage_stubs),
        work_root=Path(tempfile.mkdtemp(prefix="hss-work-")),
    )


@contextlib.contextmanager
def bind(services: Services) -> Iterator[Services]:
    token = _CURRENT.set(services)
    try:
        yield services
    finally:
        _CURRENT.reset(token)


def packet_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_id_for(data: bytes, capture_id: str) -> str:
    return run_id_for_capture(capture_id, data)


def deadline_from(clock: Clock, minutes: int) -> str:
    return (clock.now() + timedelta(minutes=minutes)).isoformat()


def branch_of(packet: dict) -> str:
    for frame in packet.get("keyframes") or []:
        depth = frame.get("depth") or {}
        if isinstance(depth, dict) and depth.get("file") and depth.get("confidenceFile"):
            return "lidar"
    return "image"
