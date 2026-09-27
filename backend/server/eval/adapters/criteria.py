"""Thin adapter over WS-09's production criteria entry point.

WS-11 does not implement the engine. A missing ``evaluate_scene`` raises
``ImportError: not_merged:ws-09`` and the gate rows SKIP. Nothing here returns a pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from server.eval.pipeline_adapters import criteria_engine
from server.eval.sceneio import validate


def _engine():
    """WS-09, through the one allowed import edge. Missing engine → None (rows SKIP)."""
    try:
        config_cls, evaluate = criteria_engine()
    except ImportError:
        return None
    return SimpleNamespace(CriteriaConfig=config_cls, evaluate_scene=evaluate)


_ENGINE_PATHS = {"C1-rules-3d", "C2-volumetric", "C1C2", "C1-rules-3d+hs11-chain"}
_PROFILES = {"A", "B", "C", "S", "public"}


@dataclass
class CriteriaConfig:
    profiles: tuple[str, ...] = ("A", "B", "C", "S", "public")
    ground_rule: str = "footprint3in"
    c2: bool = True
    c3: bool = True
    # None keeps the engine default (absent photo facts force review).
    absent_facts: str | None = None


def evaluate_scene(
    scene_dir: Path,
    out_dir: Path,
    *,
    reads: Path | None = None,
    config: CriteriaConfig | None = None,
) -> dict[str, dict]:
    """scene_dir/scene.json -> {profile: verdict v0}. Writes the engine's files under out_dir."""
    engine = _engine()
    if engine is None or not hasattr(engine, "evaluate_scene") or not hasattr(engine, "CriteriaConfig"):
        raise ImportError("not_merged:ws-09")
    cfg = config or CriteriaConfig()
    # WS-09's CriteriaConfig.profiles is a tuple. The engine writes
    # criteria_report.json with decidability.<profile>.share and returns verdicts.
    engine_kwargs = {
        "profiles": tuple(cfg.profiles),
        "ground_rule": cfg.ground_rule,
        "c2": cfg.c2,
        "c3": cfg.c3,
    }
    if cfg.absent_facts is not None:
        engine_kwargs["absent_facts"] = cfg.absent_facts
    engine_cfg = engine.CriteriaConfig(**engine_kwargs)
    produced = engine.evaluate_scene(scene_dir, out_dir, reads=reads, config=engine_cfg)
    for profile, verdict in produced.items():
        if profile not in _PROFILES:
            raise ValueError(f"engine returned an unknown rules profile {profile!r}")
        path = verdict.get("evaluator", {}).get("path")
        if path not in _ENGINE_PATHS:
            raise ValueError(f"evaluator.path {path!r} is not a criteria-engine path")
        rules_profile = verdict.get("evaluator", {}).get("rules_profile")
        if rules_profile not in _PROFILES:
            raise ValueError(f"rules_profile {rules_profile!r} is not A, B, C, S or public")
        validate(verdict)
    return produced
