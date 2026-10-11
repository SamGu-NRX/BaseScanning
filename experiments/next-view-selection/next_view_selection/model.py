"""Finite world model for the next-view selection study.

A world fixes the state of ``n_bays`` wall bays; each bay is solid ("S") or an
opening ("O").  The agent's prior knowledge restricts worlds to those whose
opening count lies in an allowed set, so the initial compatible set is finite
and enumerable.  Observation actions reveal the states of the bays inside a
station's window; movement actions change the agent's station along a line.
Every action costs budget units.  Nothing in this module lets a policy read
the hidden true world: observations are the only channel truth enters by.
"""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path

SOLID = "S"
OPENING = "O"

Action = tuple[str, str]
World = tuple[str, ...]


@dataclass(frozen=True)
class Station:
    """A viewpoint on the station line; its window is the set of bays it reveals."""

    name: str
    position: int
    window: tuple[int, ...]


@dataclass(frozen=True)
class Costs:
    observe: int
    move: int


@dataclass(frozen=True)
class StudySpec:
    """Everything the study is allowed to assume, all of it frozen in the manifest."""

    n_bays: int
    allowed_counts: tuple[int, ...]
    worlds: tuple[World, ...]
    stations: tuple[Station, ...]
    start_station: str
    budget: int
    costs: Costs
    base_seed: int
    fixed_order: tuple[str, ...]
    policy_names: tuple[str, ...]

    def station(self, name: str) -> Station:
        for station in self.stations:
            if station.name == name:
                return station
        msg = f"unknown station {name!r}"
        raise KeyError(msg)

    def station_at(self, position: int) -> Station:
        for station in self.stations:
            if station.position == position:
                return station
        msg = f"no station at position {position}"
        raise KeyError(msg)

    def seed_for(self, world_index: int) -> int:
        return self.base_seed + world_index


def load_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def enumerate_worlds(n_bays: int, allowed_counts: tuple[int, ...]) -> tuple[World, ...]:
    """All binary worlds with an opening count in ``allowed_counts``, lexicographic order."""
    worlds = []
    for states in itertools.product((OPENING, SOLID), repeat=n_bays):
        if states.count(OPENING) in allowed_counts:
            worlds.append(states)
    return tuple(sorted(worlds))


def spec_from_manifest(manifest: dict) -> StudySpec:
    world_cfg = manifest["world"]
    prior = world_cfg["prior"]
    if prior["kind"] != "exact_opening_counts":
        msg = f"unsupported prior kind {prior['kind']!r}"
        raise ValueError(msg)
    allowed_counts = tuple(prior["allowed_counts"])
    enumerated = enumerate_worlds(world_cfg["n_bays"], allowed_counts)
    frozen = tuple(tuple(w) for w in world_cfg["worlds"])
    if enumerated != frozen:
        msg = "manifest world list does not match its own enumeration rule"
        raise ValueError(msg)
    stations = tuple(
        Station(name=s["name"], position=int(s["position"]), window=tuple(s["window"]))
        for s in manifest["stations"]
    )
    budget_cfg = manifest["budget"]
    policy_cfg = manifest["policies"]
    return StudySpec(
        n_bays=world_cfg["n_bays"],
        allowed_counts=allowed_counts,
        worlds=frozen,
        stations=stations,
        start_station=manifest["start_station"],
        budget=int(budget_cfg["total"]),
        costs=Costs(observe=int(budget_cfg["observe_cost"]), move=int(budget_cfg["move_cost"])),
        base_seed=int(policy_cfg["seeded-random"]["base_seed"]),
        fixed_order=tuple(policy_cfg["fixed-order"]["order"]),
        policy_names=tuple(policy_cfg),
    )


def restriction(world: World, station: Station) -> tuple[str, ...]:
    """What an observation at ``station`` returns when ``world`` is the true world."""
    return tuple(world[bay] for bay in station.window)


def opening_count(world: World) -> int:
    return world.count(OPENING)


def entropy(n_items: int) -> float:
    """Entropy in bits of a uniform distribution over ``n_items`` items."""
    return math.log2(n_items) if n_items > 1 else 0.0


def belief_entropy(belief: frozenset[World]) -> float:
    """Entropy in bits of the current uniform belief over compatible worlds."""
    return entropy(len(belief))


def expected_entropy(belief: frozenset[World], station: Station) -> float:
    """Expected posterior entropy (bits) of observing ``station`` under a uniform belief."""
    total = len(belief)
    groups: dict[tuple[str, ...], int] = {}
    for world in belief:
        key = restriction(world, station)
        groups[key] = groups.get(key, 0) + 1
    return sum((size / total) * entropy(size) for size in groups.values())


def update_belief(
    belief: frozenset[World], station: Station, observation: tuple[str, ...]
) -> frozenset[World]:
    """Keep only worlds whose window matches what was actually observed."""
    return frozenset(w for w in belief if restriction(w, station) == observation)


def action_cost(action: Action, costs: Costs) -> int:
    kind = action[0]
    if kind == "observe":
        return costs.observe
    if kind == "move":
        return costs.move
    msg = f"unknown action kind {kind!r}"
    raise ValueError(msg)


def feasible_actions(position: str, budget: int, spec: StudySpec) -> tuple[Action, ...]:
    """Actions affordable right now, in frozen order: observe, then moves by target position."""
    actions: list[Action] = []
    current = spec.station(position)
    if spec.costs.observe <= budget:
        actions.append(("observe", position))
    for station in sorted(spec.stations, key=lambda s: s.position):
        adjacent = station.name != position and abs(station.position - current.position) == 1
        if adjacent and spec.costs.move <= budget:
            actions.append(("move", station.name))
    return tuple(actions)


@dataclass(frozen=True)
class SimState:
    """What a policy may know: its station, its belief, what it has seen, its remaining budget."""

    position: str
    belief: frozenset[World]
    seen: frozenset[int]
    budget: int


def initial_state(spec: StudySpec) -> SimState:
    return SimState(
        position=spec.start_station,
        belief=frozenset(spec.worlds),
        seen=frozenset(),
        budget=spec.budget,
    )
