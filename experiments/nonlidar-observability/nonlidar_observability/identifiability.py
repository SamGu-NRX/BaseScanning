"""Identifiability: which worlds survive an observation set, and what breaks ties.

The engine is small on purpose. Given a rig of keyframes and a finite grid of
wall worlds, every grid world is projected through the same finite camera model;
the worlds whose observation hash equals the true world's are the compatible
worlds. A fact is supported when all compatible worlds agree on it; when they
disagree, the fact is unknown to those observations, and the count of distinct
values reports how many different facts the observations still fit. The count is
grid-relative by construction: it says how many distinct fact values the tested
grid holds that stay compatible, never that all possible worlds are exhausted
(README.md, assumption A5).

Extra actions: an action adds a fixed micro-rig of two keyframes (a mini stereo
pair) to the observation set. Re-running compatibility on the extended rig says
whether the added views distinguish the worlds. Actions are the study's answer
to "what would settle it": only added observations can distinguish worlds.
"""

from __future__ import annotations

from dataclasses import dataclass

from .observe import Observation, observation_hash, observe
from .projection import Camera
from .worlds import WallWorld, grid_worlds

_FIELD = {
    "orientation_deg": "theta_deg",
    "distance_ft": "d_ft",
    "left_end_ft": "s0_ft",
    "right_end_ft": "s1_ft",
    "height_ft": "h_ft",
}


@dataclass(frozen=True)
class FactVerdict:
    """One fact's fate under one observation set."""

    fact: str
    values: tuple[float, ...]  # distinct fact values among compatible worlds
    pair: tuple[str, str] | None  # true world and one differing compatible world

    @property
    def supported(self) -> bool:
        return len(self.values) == 1

    @property
    def count(self) -> int:
        return len(self.values)


@dataclass(frozen=True)
class Compatibility:
    """The outcome of one observation set against the grid."""

    observation: Observation
    observation_hash: str
    compatible_count: int
    grid_count: int
    verdicts: tuple[FactVerdict, ...]

    def verdict(self, fact: str) -> FactVerdict:
        return next(v for v in self.verdicts if v.fact == fact)

    @property
    def unknown_facts(self) -> tuple[str, ...]:
        return tuple(v.fact for v in self.verdicts if not v.supported)


def evaluate(
    true_world: WallWorld,
    cameras: list[Camera],
    grid: dict,
    grid_worlds_list: list[WallWorld] | None = None,
) -> Compatibility:
    """Project the true world and every grid world through the rig; compare."""
    obs = observe(true_world, cameras)
    base = observation_hash(obs)
    worlds = grid_worlds_list if grid_worlds_list is not None else list(grid_worlds(grid))
    compatible = [w for w in worlds if observation_hash(observe(w, cameras)) == base]
    verdicts = []
    for fact, field in _FIELD.items():
        values = tuple(sorted({getattr(w, field) for w in compatible}))
        other = next(
            (w for w in compatible if getattr(w, field) != getattr(true_world, field)), None
        )
        pair = (true_world.describe(), other.describe()) if other is not None else None
        verdicts.append(FactVerdict(fact=fact, values=values, pair=pair))
    return Compatibility(
        observation=obs,
        observation_hash=base,
        compatible_count=len(compatible),
        grid_count=len(worlds),
        verdicts=tuple(verdicts),
    )
