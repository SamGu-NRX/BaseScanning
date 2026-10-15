"""Per-area knobs HSS_OBJECTS_* and the small types the stage passes around."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MODELS = {"cheap": "gpt-5.4-mini", "confirm": "gpt-5.5", "second": "grok-4.7"}
PLANNED_USD = {"gpt-5.4-mini": 0.0011, "gpt-5.5": 0.0167, "gpt-5.5:boxes": 0.054, "grok-4.7": 0.029}
XAI_BASE_URL = "https://api.x.ai/v1"

# List prices, USD per million tokens. Used when a vendor does not return a cost.
TOKEN_PRICES = {
    "gpt-5.5": (5.0, 30.0),
    "gpt-5.4-mini": (0.75, 4.5),
    "grok-4.7": (2.0, 6.0),
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "on", "yes"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class ObjectsConfig:
    # In-process opt-in for tests that build a config by hand. `from_env` stays off
    # until VISION_EXTERNAL_CALLS is explicitly on (SD-67).
    external_vision: bool = True
    budget_usd: float = 0.85
    gate_models: tuple[str, ...] = ("clip", "siglip")
    top_k_per_class: int = 2
    max_selected_frames: int = 20
    proposer_seed_thr: float = 0.3
    proposer_support_thr: float = 0.2
    gdino_openings: bool = True
    second_reader: str = "grok-4.7"
    max_side_px: int = 1024
    openai_timeout_s: float = 90.0
    grok_timeout_s: float = 120.0
    cache_dir: str = "objects/vlm-cache"
    device: str = "auto"
    seed: int = 0
    owl_tile: bool = False

    @classmethod
    def from_env(cls, settings: object | None = None) -> ObjectsConfig:
        """Read HSS_OBJECTS_* . Vendor calls follow an explicit VISION_EXTERNAL_CALLS (SD-67)."""
        external = _external_vision(settings)
        gates = os.environ.get("HSS_OBJECTS_GATE_MODELS", "clip,siglip")
        return cls(
            external_vision=external,
            budget_usd=_env_float("HSS_OBJECTS_VLM_BUDGET_USD_PER_HOUSE", 0.85),
            gate_models=tuple(p.strip() for p in gates.split(",") if p.strip()),
            top_k_per_class=_env_int("HSS_OBJECTS_GATE_TOP_K", 2),
            max_selected_frames=_env_int("HSS_OBJECTS_MAX_SELECTED", 20),
            proposer_seed_thr=_env_float("HSS_OBJECTS_OWL_SEED_THR", 0.3),
            proposer_support_thr=_env_float("HSS_OBJECTS_OWL_SUPPORT_THR", 0.2),
            gdino_openings=_env_bool("HSS_OBJECTS_GDINO_OPENINGS", True),
            second_reader=os.environ.get("HSS_OBJECTS_SECOND_READER", "grok-4.7"),
            max_side_px=_env_int("HSS_OBJECTS_MAX_SIDE_PX", 1024),
            openai_timeout_s=_env_float("HSS_OBJECTS_OPENAI_TIMEOUT_S", 90.0),
            grok_timeout_s=_env_float("HSS_OBJECTS_GROK_TIMEOUT_S", 120.0),
            cache_dir=os.environ.get("HSS_OBJECTS_CACHE_DIR", "objects/vlm-cache"),
            device=os.environ.get("HSS_OBJECTS_DEVICE", "auto"),
            seed=_env_int("HSS_OBJECTS_SEED", 0),
            owl_tile=_env_bool("HSS_OBJECTS_OWL_TILE_2X2", False),
        )


_ON = {"1", "true", "on", "yes"}


def _is_on(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    return str(raw).strip().lower() in _ON


def _external_from_env() -> bool:
    """Unset or blank is off, including prod. Only an explicit on-value authorizes a call."""
    raw = os.environ.get("VISION_EXTERNAL_CALLS")
    if raw is None or str(raw).strip() == "":
        return False
    return _is_on(raw)


def _external_vision(settings: object | None) -> bool:
    """SD-67: off unless this process set VISION_EXTERNAL_CALLS.

    WS-00 Settings fills ``on`` in non-prod when the variable is unset. That fill
    is not authorization. An explicit settings value of off still refuses the call.
    """
    if not _external_from_env():
        return False
    if settings is None or not hasattr(settings, "VISION_EXTERNAL_CALLS"):
        return True
    raw = settings.VISION_EXTERNAL_CALLS
    if raw is None:
        return True
    return _is_on(raw)


def effective_budget(cfg: ObjectsConfig, ctx_budget: float | None) -> float:
    """SD-56: the lower of the stage cap and StageContext.vlm_budget_usd wins."""
    if ctx_budget is None:
        return cfg.budget_usd
    return min(cfg.budget_usd, float(ctx_budget))


@dataclass
class FrameRef:
    id: str
    image: Path
    w: int
    h: int
    kind: str  # keyframe | still
    purpose: str | None = None
    epoch: str | None = None
    sha256: str = ""
    house: str = ""


@dataclass
class Detection:
    label: str
    score: float
    box: list[float]  # normalised xyxy

    def as_dict(self) -> dict:
        return {"label": self.label, "score": self.score, "box": self.box}


@dataclass
class FrameSelection:
    frames: list[str]
    hazard_frames: list[str]
    filtered_frac: float
    by_house: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Cluster:
    cls: str
    pos_m: list[float]
    views: list[dict]
    spread_m: float
    method: str
    wall_id: str | None = None
    s_m: list[float] | None = None
    bottom_m: float | None = None
    top_m: float | None = None
    size_m: list[float] | None = None


class ObjectsError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
