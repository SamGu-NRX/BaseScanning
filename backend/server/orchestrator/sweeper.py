"""Re-spawn a dead coordinator and enforce the hard stop. Returns runs touched."""

from __future__ import annotations

from server.common.logging import log_event
from server.orchestrator.clock import parse_iso
from server.orchestrator.coordinator import RunCoordinator
from server.orchestrator.executor import CoordinatorDied
from server.orchestrator.outcomes import Facts, decide
from server.orchestrator.services import Services
from server.orchestrator.stateio import get_json, put_json


def coordinator_is_dead(run, raw: dict, *, now, stale_s: float) -> bool:
    """A missing heartbeat is dead only after the run itself is older than ``stale_s``.

    Two finish threads can observe a run before its first heartbeat write. Treating
    that gap as a dead coordinator starts a second drive and resets the stage rows.
    """

    beat = raw.get("coordinator_heartbeat_at")
    if beat:
        return (now - parse_iso(str(beat))).total_seconds() > stale_s
    anchor = run.started_at or run.created_at
    return (now - parse_iso(str(anchor))).total_seconds() > stale_s


def sweep(services: Services, *, retry_schedule: list[float] | None = None) -> int:
    touched = 0
    runs = services.state.list_runs(status={"pending", "running"})  # type: ignore[attr-defined]
    for run in runs:
        raw = dict(get_json(services.state, f"run_control:{run.run_id}") or {})
        dead = coordinator_is_dead(
            run,
            raw,
            now=services.clock.now(),
            stale_s=services.orch.stale_s,
        )
        if dead and run.status in {"pending", "running"}:
            raw["coordinator_respawns"] = int(raw.get("coordinator_respawns") or 0) + 1
            put_json(services.state, f"run_control:{run.run_id}", raw)
            if raw["coordinator_respawns"] <= 2:
                hooks = get_json(services.state, f"run_hooks:{run.run_id}") or {}
                if hooks.get("modal_remote") and _spawn_coordinator(run.run_id):
                    touched += 1
                    continue
                try:
                    RunCoordinator(services).drive(run.run_id)
                except CoordinatorDied:
                    pass
            else:
                fresh = services.state.get_run(run.run_id)  # type: ignore[attr-defined]
                if fresh is not None:
                    RunCoordinator(services)._close(fresh, decide(Facts(coordinator_dead=True)), "coordinator dead")
            touched += 1
            continue
        if services.clock.now() >= parse_iso(run.deadline_at):
            RunCoordinator(services).tick(run.run_id)
            touched += 1
    touched += retry_failed_webhooks(services, schedule=retry_schedule)
    log_event(
        "sweep",
        msg="sweeper pass",
        **{"service.name": services.settings.SMP_SERVICE_NAME},
    )
    return touched


def retry_failed_webhooks(services: Services, *, schedule: list[float] | None = None) -> int:
    """Record the next delay for deliveries WS-10 left in ``webhook_failed``.

    The list is ``server.result.notify.retry_schedule`` once that module is
    imported. Callers may pass the same list. This pass does not POST.
    """

    delays = schedule if schedule is not None else _notify_schedule()
    if not delays:
        return 0
    touched = 0
    for key in services.state.list_blobs("webhook:"):  # type: ignore[attr-defined]
        raw = get_json(services.state, key) or {}
        if raw.get("status") != "webhook_failed":
            continue
        attempt = int(raw.get("attempt") or 0)
        if attempt >= len(delays):
            continue
        raw["next_delay_s"] = delays[attempt]
        raw["attempt"] = attempt + 1
        put_json(services.state, key, raw)
        touched += 1
    return touched


def _spawn_coordinator(run_id: str) -> bool:
    """Re-attach on Modal. Imported lazily: ``modal_functions`` imports ``api``, which imports this module."""

    from server.orchestrator.modal_functions import coordinate_run, modal_available

    if not modal_available():
        return False
    coordinate_run.spawn(run_id)
    return True


def _notify_schedule() -> list[float]:
    try:
        from server.result.notify import retry_schedule
    except ImportError:
        return []
    loaded = retry_schedule()
    if not isinstance(loaded, list):
        return []
    return [float(item) for item in loaded]
