"""The four view-selection policies.

Every policy sees the same state: its station, its belief (the compatible
world set), which bays it has observed, and its remaining budget.  No policy
receives the hidden true world.  All choices are deterministic given the
state, except seeded-random, which draws from the run's RNG.
"""

from __future__ import annotations

import random

from .model import (
    Action,
    SimState,
    StudySpec,
    belief_entropy,
    expected_entropy,
    feasible_actions,
)

EPS = 1e-12


class UncertaintyReduction:
    """Greedy expected-entropy reduction per cost unit.

    Observes the current station whenever that would reduce expected posterior
    entropy (the belief is uniform over compatible worlds); skips zero-gain
    observations.  Otherwise moves toward the adjacent station whose window
    holds the most unseen bays (ties: left).  A move always has zero immediate
    information gain, so the myopic rule never rates moving above an
    informative observation — the fallback exists because refusing to move
    would stall the run.
    """

    name = "uncertainty-reduction"

    def choose(self, state: SimState, spec: StudySpec, rng: random.Random) -> Action | None:
        del rng  # deterministic policy
        current = spec.station(state.position)
        if spec.costs.observe <= state.budget:
            gain = belief_entropy(state.belief) - expected_entropy(state.belief, current)
            if gain > EPS:
                return ("observe", state.position)
        return _fallback_move(state, spec)


class NearestUnseen:
    """Observe the current station if it still hides a bay; else walk to the nearest one.

    Ties between equidistant stations break left.  Repeat observations never
    happen: a station whose window is fully seen has no unseen bays.
    """

    name = "nearest-unseen"

    def choose(self, state: SimState, spec: StudySpec, rng: random.Random) -> Action | None:
        del rng  # deterministic policy
        current = spec.station(state.position)
        if spec.costs.observe <= state.budget and set(current.window) - state.seen:
            return ("observe", state.position)
        hidden = [s for s in spec.stations if set(s.window) - state.seen]
        if not hidden:
            return None
        hidden.sort(key=lambda s: (abs(s.position - current.position), s.position))
        goal = hidden[0]
        step = 1 if goal.position > current.position else -1
        nxt = spec.station_at(current.position + step)
        if spec.costs.move <= state.budget:
            return ("move", nxt.name)
        return None


class FixedOrder:
    """Follow the frozen station order literally, informative or not.

    Move to each target in turn, observe it, continue.  Stops early when
    resolved or when the next action does not fit the remaining budget.
    """

    name = "fixed-order"

    def __init__(self, order: tuple[str, ...]) -> None:
        self.order = tuple(order)
        self._index = 0

    def choose(self, state: SimState, spec: StudySpec, rng: random.Random) -> Action | None:
        del rng  # deterministic policy
        while self._index < len(self.order):
            target = spec.station(self.order[self._index])
            if state.position != target.name:
                step = 1 if target.position > spec.station(state.position).position else -1
                nxt = spec.station_at(spec.station(state.position).position + step)
                if spec.costs.move <= state.budget:
                    return ("move", nxt.name)
                return None
            if spec.costs.observe <= state.budget:
                self._index += 1
                return ("observe", state.position)
            return None
        return None


class SeededRandom:
    """Uniform choice among feasible actions each step; repeat observations allowed."""

    name = "seeded-random"

    def choose(self, state: SimState, spec: StudySpec, rng: random.Random) -> Action | None:
        actions = feasible_actions(state.position, state.budget, spec)
        if not actions:
            return None
        return rng.choice(list(actions))


def _fallback_move(state: SimState, spec: StudySpec) -> Action | None:
    """Move toward the adjacent station whose window holds the most unseen bays (ties: left)."""
    best: tuple[tuple[int, int], Action] | None = None
    for action in feasible_actions(state.position, state.budget, spec):
        if action[0] != "move":
            continue
        target = spec.station(action[1])
        unseen = len(set(target.window) - state.seen)
        key = (-unseen, target.position)
        if best is None or key < best[0]:
            best = (key, action)
    return best[1] if best else None


def make_policies(spec: StudySpec) -> dict[str, object]:
    """Instantiate the manifest's policies in manifest order."""
    built: dict[str, object] = {}
    for name in spec.policy_names:
        if name == "uncertainty-reduction":
            built[name] = UncertaintyReduction()
        elif name == "nearest-unseen":
            built[name] = NearestUnseen()
        elif name == "fixed-order":
            built[name] = FixedOrder(spec.fixed_order)
        elif name == "seeded-random":
            built[name] = SeededRandom()
        else:
            msg = f"unknown policy {name!r}"
            raise ValueError(msg)
    return built
