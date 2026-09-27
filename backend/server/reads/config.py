"""Per-area knobs `HSS_READS_*`. The shared vision switch is `VISION_EXTERNAL_CALLS`."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "on", "true", "yes"}


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else float(raw)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else int(raw)


def vision_external_calls(settings: object | None = None) -> bool:
    """WS-00 `Settings.VISION_EXTERNAL_CALLS`, else the env var (default on)."""
    if settings is not None and hasattr(settings, "VISION_EXTERNAL_CALLS"):
        value = settings.VISION_EXTERNAL_CALLS
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "on", "true", "yes"}
    return _flag("VISION_EXTERNAL_CALLS", True)


@dataclass(frozen=True)
class ReadsConfig:
    external_vision: bool = True
    autofill_without_barcode: bool = False
    panel_autopass: bool = False
    read_oblique: bool = True
    max_side_px: int = 2048
    gpt_timeout_s: float = 60.0
    grok_timeout_s: float = 120.0
    mini_timeout_s: float = 30.0
    attempts: int = 3
    still_deadline_s: float = 300.0
    budget_usd_per_house: float = 0.40
    cache: bool = True
    models: tuple[str, str, str] = ("gpt-5.5", "grok-4.7", "gpt-5.4-mini")
    panel_prompt_id: str = "panel/v0"
    cache_dir: str | None = None

    @classmethod
    def from_env(cls, settings: object | None = None) -> ReadsConfig:
        return cls(
            external_vision=vision_external_calls(settings),
            autofill_without_barcode=_flag("HSS_READS_AUTOFILL_NO_BARCODE", False),
            panel_autopass=_flag("HSS_READS_PANEL_AUTOPASS", False),
            read_oblique=_flag("HSS_READS_READ_OBLIQUE", True),
            max_side_px=_int("HSS_READS_MAX_SIDE_PX", 2048),
            gpt_timeout_s=_float("HSS_READS_GPT_TIMEOUT_S", 60.0),
            grok_timeout_s=_float("HSS_READS_GROK_TIMEOUT_S", 120.0),
            mini_timeout_s=_float("HSS_READS_MINI_TIMEOUT_S", 30.0),
            attempts=_int("HSS_READS_ATTEMPTS", 3),
            still_deadline_s=_float("HSS_READS_STILL_DEADLINE_S", 300.0),
            budget_usd_per_house=_float("HSS_READS_BUDGET_USD_PER_HOUSE", 0.40),
            cache=_flag("HSS_READS_CACHE", True),
            panel_prompt_id=os.environ.get("HSS_READS_PANEL_PROMPT", "panel/v0"),
            cache_dir=os.environ.get("HSS_READS_CACHE_DIR") or None,
        )

    def timeout_for(self, model: str) -> float:
        if model.startswith("grok"):
            return self.grok_timeout_s
        if model.endswith("-mini"):
            return self.mini_timeout_s
        return self.gpt_timeout_s

    def effective_budget(self, stage_budget_usd: float | None) -> float:
        """SD-56: inside a stage the lower of the stage budget and the reads cap wins."""
        if stage_budget_usd is None:
            return self.budget_usd_per_house
        return min(self.budget_usd_per_house, stage_budget_usd)
