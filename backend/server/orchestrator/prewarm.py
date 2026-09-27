"""GPU and EarlyQa prewarm. One per capture, skipped when the spend caps say so."""

from __future__ import annotations

import asyncio
import os

from server.common.deploy_switch import modal_app_name
from server.common.logging import log_event
from server.contracts.packet import Tier
from server.orchestrator.clock import parse_iso
from server.orchestrator.services import Services
from server.orchestrator.spend import KEEP_WARM_USD, prewarm_blocked
from server.orchestrator.stateio import get_json, put_json


def request_prewarm(
    services: Services,
    capture_id: str,
    *,
    tier: Tier,
    expected_keyframes: int | None = None,
) -> None:
    del expected_keyframes
    if get_json(services.state, f"prewarm:{capture_id}"):
        return
    last = get_json(services.state, "gpu_last_call_at")
    age = None
    if last:
        age = (services.clock.now() - parse_iso(str(last))).total_seconds()
    if prewarm_blocked(
        total_usd=services.state.get_spend("total"),  # type: ignore[attr-defined]
        gpu_day_usd=services.state.get_spend(f"gpu:day:{services.clock.now().date().isoformat()}"),  # type: ignore[attr-defined]
        last_call_age_s=age,
        config=services.orch,
    ):
        return
    spawned = _spawn_warm()
    put_json(
        services.state,
        f"prewarm:{capture_id}",
        {
            "at": services.clock.iso(),
            "tier": tier,
            "classes": ["Pipeline", "EarlyQa"],
            "spawned": spawned,
        },
    )
    services.state.add_spend("gpu", KEEP_WARM_USD)  # type: ignore[attr-defined]
    services.state.add_spend("total", KEEP_WARM_USD)  # type: ignore[attr-defined]
    day = services.clock.now().date().isoformat()
    services.state.add_spend(f"gpu:day:{day}", KEEP_WARM_USD)  # type: ignore[attr-defined]
    put_json(services.state, "gpu_last_call_at", services.clock.iso())


def _spawn_call(spawn: object) -> None:
    """``.spawn()`` raises inside an async request. Use ``.spawn.aio`` on a running loop."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        spawn()  # type: ignore[operator]
        return
    aio = getattr(spawn, "aio", None)
    if aio is None:
        spawn()  # type: ignore[operator]
        return
    asyncio.get_running_loop().create_task(aio())


def _warm_spawns() -> tuple[object, object] | None:
    """Handles for ``Pipeline.warm`` and ``EarlyQa.warm``.

    Inside a container the imported ``App`` is not the deployed one, so ``.spawn``
    raises ``ExecutionError`` (app is not running). Look the classes up by name.
    """

    from server.orchestrator.modal_functions import EarlyQa, Pipeline, modal_available

    if not modal_available():
        return None
    if os.environ.get("MODAL_TASK_ID"):
        import modal

        app_name = modal_app_name()
        log_event("modal_spawn", msg=f"app={app_name} class=Pipeline")
        log_event("modal_spawn", msg=f"app={app_name} class=EarlyQa")
        pipeline = modal.Cls.from_name(app_name, "Pipeline")
        early = modal.Cls.from_name(app_name, "EarlyQa")
        return pipeline().warm.spawn, early().warm.spawn
    return Pipeline().warm.spawn, EarlyQa().warm.spawn


def _spawn_warm() -> dict[str, bool]:
    """Ask Modal to enter Pipeline and EarlyQa. Absent Modal records the request only.

    Imported lazily: ``modal_functions`` imports ``api``, which imports this module.
    """

    handles = _warm_spawns()
    if handles is None:
        return {"pipeline": False, "early_qa": False}
    pipeline_spawn, early_spawn = handles
    _spawn_call(pipeline_spawn)
    _spawn_call(early_spawn)
    return {"pipeline": True, "early_qa": True}
