"""Orchestrator knobs. Env names are ``HSS_ORCH_*`` (SD-11)."""

from __future__ import annotations

import math
import os

from pydantic import BaseModel, ConfigDict


def _flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


_LOCAL_OUTCOMES = (
    "manual_review",
    "eligible",
    "needs_more_photos",
    "not_eligible",
    "failed",
)


def _dev_stubs(env: dict[str, str]) -> bool:
    """Scripted local outcomes are honored only under dev stage stubs."""
    return _flag(env.get("HSS_ORCH_STAGE_STUBS", "0")) and env.get("SMP_ENVIRONMENT", "prod") == "dev"


def _require_dev_stubs(name: str, env: dict[str, str]) -> None:
    if _dev_stubs(env):
        return
    raise ValueError(
        f"{name} is only honored with HSS_ORCH_STAGE_STUBS=1 and SMP_ENVIRONMENT=dev "
        f"(got HSS_ORCH_STAGE_STUBS={env.get('HSS_ORCH_STAGE_STUBS', '0')!r}, "
        f"SMP_ENVIRONMENT={env.get('SMP_ENVIRONMENT', '(unset)')!r}); "
        "refusing to run with it silently ignored"
    )


def _local_outcome(env: dict[str, str]) -> str:
    raw = env.get("HSS_LOCAL_OUTCOME", "")
    if not raw:
        return "manual_review"
    if raw not in _LOCAL_OUTCOMES:
        raise ValueError(f"HSS_LOCAL_OUTCOME={raw!r} is not one of {', '.join(_LOCAL_OUTCOMES)}")
    _require_dev_stubs("HSS_LOCAL_OUTCOME", env)
    return raw


def _local_stage_delay_s(env: dict[str, str]) -> float:
    raw = env.get("HSS_LOCAL_STAGE_DELAY_S", "")
    if not raw:
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"HSS_LOCAL_STAGE_DELAY_S={raw!r} is not a number of seconds") from None
    if not math.isfinite(value):
        raise ValueError(f"HSS_LOCAL_STAGE_DELAY_S={raw!r} is not a finite number of seconds")
    if not 0 <= value <= 30:
        raise ValueError(f"HSS_LOCAL_STAGE_DELAY_S={raw!r} is outside the range 0 to 30 seconds")
    _require_dev_stubs("HSS_LOCAL_STAGE_DELAY_S", env)
    return value


class OrchConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stage_stubs: bool = False
    heartbeat_s: float = 30
    stale_s: float = 180
    max_respawns: int = 2
    sweeper_period_min: int = 5
    gpu_primary: str = "L4"
    gpu_escalate: str = "A100-40GB"
    prewarm: bool = True
    modal_spend_cap_usd: float = 200
    gpu_daily_cap_usd: float = 20
    vlm_run_soft_usd: float = 0.30
    vlm_run_hard_usd: float = 1.50
    vlm_call_timeout_s: float = 120
    vlm_tries: int = 3
    test_hang_stage: str = ""
    test_fail_stage: str = ""
    test_kill_coordinator_after: str = ""
    # One second, so a stale cancel lands inside 180–185s and a hard stop
    # cancels a live worker within 15s of the deadline.
    coordinator_loop_s: float = 1
    modal_remote: bool = False
    # Dev-only scripted outcome for the stage stubs; see the README's Stubs
    # section. Both are refused loudly outside dev stage stubs.
    local_outcome: str = "manual_review"
    local_stage_delay_s: float = 0.0


def load_orch(environ: dict[str, str] | None = None) -> OrchConfig:
    env = os.environ if environ is None else environ

    def pick(name: str, default: str) -> str:
        return env.get(name, default)

    return OrchConfig(
        stage_stubs=_flag(pick("HSS_ORCH_STAGE_STUBS", "0")),
        heartbeat_s=float(pick("HSS_ORCH_HEARTBEAT_S", "30")),
        stale_s=float(pick("HSS_ORCH_STALE_S", "180")),
        max_respawns=int(pick("HSS_ORCH_MAX_RESPAWNS", "2")),
        sweeper_period_min=int(pick("HSS_ORCH_SWEEPER_PERIOD_MIN", "5")),
        gpu_primary=pick("HSS_ORCH_GPU_PRIMARY", "L4"),
        gpu_escalate=pick("HSS_ORCH_GPU_ESCALATE", "A100-40GB"),
        prewarm=_flag(pick("HSS_ORCH_PREWARM", "1")),
        modal_spend_cap_usd=float(pick("HSS_ORCH_MODAL_SPEND_CAP_USD", "200")),
        gpu_daily_cap_usd=float(pick("HSS_ORCH_GPU_DAILY_CAP_USD", "20")),
        vlm_run_soft_usd=float(pick("HSS_ORCH_VLM_RUN_SOFT_USD", "0.30")),
        vlm_run_hard_usd=float(pick("HSS_ORCH_VLM_RUN_HARD_USD", "1.50")),
        vlm_call_timeout_s=float(pick("HSS_ORCH_VLM_CALL_TIMEOUT_S", "120")),
        vlm_tries=int(pick("HSS_ORCH_VLM_TRIES", "3")),
        test_hang_stage=pick("HSS_ORCH_TEST_HANG_STAGE", ""),
        test_fail_stage=pick("HSS_ORCH_TEST_FAIL_STAGE", ""),
        test_kill_coordinator_after=pick("HSS_ORCH_TEST_KILL_COORDINATOR_AFTER", ""),
        coordinator_loop_s=float(pick("HSS_ORCH_COORDINATOR_LOOP_S", "1")),
        modal_remote=_flag(pick("HSS_ORCH_MODAL_REMOTE", "0")),
        local_outcome=_local_outcome(env),
        local_stage_delay_s=_local_stage_delay_s(env),
    )
