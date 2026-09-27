"""GPU and EarlyQa prewarm. One per capture, skipped when the spend caps say so."""

from __future__ import annotations

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


def _spawn_warm() -> dict[str, bool]:
    """Ask Modal to enter Pipeline and EarlyQa. Absent Modal records the request only.

    Imported lazily: ``modal_functions`` imports ``api``, which imports this module.
    """

    from server.orchestrator.modal_functions import EarlyQa, Pipeline, modal_available

    if not modal_available():
        return {"pipeline": False, "early_qa": False}
    Pipeline().warm.spawn()
    EarlyQa().warm.spawn()
    return {"pipeline": True, "early_qa": True}
