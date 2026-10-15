"""GPU, CPU and VLM cost. Rates are the measured ``internal/modal-pipeline/app.py`` table."""

from __future__ import annotations

from server.orchestrator.config import OrchConfig

# USD per second. L4 in that file is 0.000222, not the brief's 0.000380.
GPU_USD_PER_S = {
    "L4": 0.000222,
    "A10G": 0.000306,
    "A100": 0.000583,
    "A100-40GB": 0.000583,
    "A100-80GB": 0.000694,
    "H100": 0.001097,
}
CPU_USD_PER_S = 0.0000131
# Pipeline class uses 8 CPU. Early workers use 2. Stage attempts use 8 on GPU and 2 on CPU.
GPU_CPU = 8
CPU_CPU = 2
# 900 s keep-warm at the L4 rate. The brief's $0.34 used 0.000380/s, which is not in app.py.
KEEP_WARM_S = 900
KEEP_WARM_USD = round(KEEP_WARM_S * GPU_USD_PER_S["L4"], 6)


def rate(gpu: str | None) -> float:
    if gpu:
        return GPU_USD_PER_S[gpu] + GPU_CPU * CPU_USD_PER_S
    return CPU_CPU * CPU_USD_PER_S


def attempt_usd(duration_s: float, gpu: str | None) -> float:
    return duration_s * rate(gpu)


def vlm_budget_usd(spent: float, config: OrchConfig) -> float:
    """Hits 0 exactly when spend reaches the hard cap."""
    if spent >= config.vlm_run_hard_usd:
        return 0.0
    return config.vlm_run_hard_usd - spent


def over_soft_baseline(spent: float, config: OrchConfig) -> bool:
    return spent > config.vlm_run_soft_usd


def gpu_spawn_blocked(total_usd: float, config: OrchConfig) -> bool:
    return total_usd >= 0.9 * config.modal_spend_cap_usd


def prewarm_blocked(*, total_usd: float, gpu_day_usd: float, last_call_age_s: float | None, config: OrchConfig) -> bool:
    if not config.prewarm:
        return True
    if total_usd >= 0.8 * config.modal_spend_cap_usd:
        return True
    if gpu_day_usd >= config.gpu_daily_cap_usd:
        return True
    if last_call_age_s is not None and last_call_age_s < 840:
        return True
    return False
