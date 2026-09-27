"""Orchestrator knobs. Env names are ``HSS_ORCH_*`` (SD-11)."""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict


def _flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
    )
