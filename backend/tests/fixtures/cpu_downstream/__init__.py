"""Fixture helpers for the real CPU downstream stages (criteria, result).

``scenes.supported_wall_variants(dest)`` writes two supported single-wall source
scenes — the synthetic flat-wall generator's geometry unchanged, plus one variant
carrying an observed gas-meter hazard at known dimensions. See ``scenes.py`` for
the callable and the relative artifact layout.
"""

from .scenes import (
    HAZARD_BOTTOM_M,
    HAZARD_S_M,
    HAZARD_TOP_M,
    VARIANTS,
    hazard_object,
    original_scene,
    supported_wall_variants,
    write_variant,
)

__all__ = [
    "HAZARD_BOTTOM_M",
    "HAZARD_S_M",
    "HAZARD_TOP_M",
    "VARIANTS",
    "hazard_object",
    "original_scene",
    "supported_wall_variants",
    "write_variant",
]
