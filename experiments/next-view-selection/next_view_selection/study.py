"""Study runner: simulate runs, aggregate metrics, and prove impossible-view cases.

The run loop is the only place truth enters a run: an observation reads the
true world's window and the belief filters on it.  The exhaustive search in
this module is an ANALYSIS ORACLE — it tries every action sequence against
the true world to establish which worlds no policy could ever resolve within
the frozen budget.  It is never available to policies.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from .model import (
    SimState,
    StudySpec,
    World,
    action_cost,
    feasible_actions,
    initial_state,
    restriction,
    update_belief,
)
from .policies import make_policies

MAX_STEPS = 64  # guard against runaway loops; the budget makes it unreachable in practice

RUN_FIELDS = (
    "world_index",
    "world",
    "policy",
    "seed",
    "resolved",
    "answer",
    "unsupported",
    "guess_correct",
    "cost_used",
    "observations",
    "moves",
    "final_belief_size",
    "actions",
)

SUMMARY_FIELDS = (
    "policy",
    "runs",
    "resolved",
    "resolution_rate",
    "mean_cost",
    "mean_cost_resolved",
    "unsupported",
    "unsupported_rate",
    "unsupported_correct_guesses",
    "no_resolution_worlds",
)

IMPOSSIBLE_FIELDS = (
    "world_index",
    "world",
    "oracle_resolvable",
    "resolved_by_uncertainty-reduction",
    "resolved_by_nearest-unseen",
    "resolved_by_fixed-order",
    "resolved_by_seeded-random",
)


@dataclass
class RunResult:
    world_index: int
    world: World
    policy: str
    seed: int
    resolved: bool
    answer: World
    unsupported: bool
    guess_correct: bool
    cost_used: int
    observations: int
    moves: int
    final_belief_size: int
    actions: list[str] = field(default_factory=list)

    def as_row(self) -> dict[str, object]:
        return {
            "world_index": self.world_index,
            "world": "".join(self.world),
            "policy": self.policy,
            "seed": self.seed,
            "resolved": self.resolved,
            "answer": "".join(self.answer),
            "unsupported": self.unsupported,
            "guess_correct": self.guess_correct,
            "cost_used": self.cost_used,
            "observations": self.observations,
            "moves": self.moves,
            "final_belief_size": self.final_belief_size,
            "actions": " ".join(self.actions),
        }


def simulate(
    world: World, world_index: int, policy_name: str, policy, spec: StudySpec
) -> RunResult:
    """Run one policy against one hidden world under the shared frozen budget."""
    rng = random.Random(spec.seed_for(world_index))
    state = initial_state(spec)
    observations = moves = cost_used = 0
    trace: list[str] = []
    for _ in range(MAX_STEPS):
        if len(state.belief) == 1:
            break
        action = policy.choose(state, spec, rng)
        if action is None:
            break
        cost = action_cost(action, spec.costs)
        if action[0] == "observe":
            station = spec.station(action[1])
            observed = restriction(world, station)
            state = SimState(
                position=state.position,
                belief=update_belief(state.belief, station, observed),
                seen=state.seen | set(station.window),
                budget=state.budget - cost,
            )
            observations += 1
        else:
            state = SimState(
                position=action[1],
                belief=state.belief,
                seen=state.seen,
                budget=state.budget - cost,
            )
            moves += 1
        cost_used += cost
        trace.append(f"{action[0]}:{action[1]}")
    resolved = len(state.belief) == 1
    answer = next(iter(state.belief)) if resolved else min(state.belief)
    return RunResult(
        world_index=world_index,
        world=world,
        policy=policy_name,
        seed=spec.seed_for(world_index),
        resolved=resolved,
        answer=answer,
        unsupported=not resolved,
        guess_correct=answer == world,
        cost_used=cost_used,
        observations=observations,
        moves=moves,
        final_belief_size=len(state.belief),
        actions=trace,
    )


def run_study(spec: StudySpec) -> list[RunResult]:
    """Every frozen world x every manifest policy, equal budgets, deterministic seeds."""
    policies = make_policies(spec)
    results: list[RunResult] = []
    for world_index, world in enumerate(spec.worlds):
        for policy_name in spec.policy_names:
            results.append(simulate(world, world_index, policy_name, policies[policy_name], spec))
    return results


def summarize(results: list[RunResult], spec: StudySpec) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for policy_name in spec.policy_names:
        runs = [r for r in results if r.policy == policy_name]
        resolved = [r for r in runs if r.resolved]
        unsupported = [r for r in runs if r.unsupported]
        rows.append(
            {
                "policy": policy_name,
                "runs": len(runs),
                "resolved": len(resolved),
                "resolution_rate": len(resolved) / len(runs),
                "mean_cost": sum(r.cost_used for r in runs) / len(runs),
                "mean_cost_resolved": (
                    sum(r.cost_used for r in resolved) / len(resolved) if resolved else ""
                ),
                "unsupported": len(unsupported),
                "unsupported_rate": len(unsupported) / len(runs),
                "unsupported_correct_guesses": sum(r.guess_correct for r in unsupported),
                "no_resolution_worlds": " ".join(
                    str(r.world_index) for r in runs if not r.resolved
                ),
            }
        )
    return rows


def oracle_resolvable(world: World, spec: StudySpec) -> bool:
    """ANALYSIS ORACLE.  True when some action sequence, scored against the true
    world's actual observations, drives the belief to the true world alone."""

    @cache
    def dfs(position: str, budget: int, belief: frozenset[World], seen: frozenset[int]) -> bool:
        if len(belief) == 1:
            return True
        for action in feasible_actions(position, budget, spec):
            cost = action_cost(action, spec.costs)
            if action[0] == "observe":
                station = spec.station(action[1])
                nxt_belief = update_belief(belief, station, restriction(world, station))
                if dfs(position, budget - cost, nxt_belief, seen | set(station.window)):
                    return True
            else:
                if dfs(action[1], budget - cost, belief, seen):
                    return True
        return False

    start = initial_state(spec)
    return dfs(start.position, start.budget, start.belief, start.seen)


def impossible_table(results: list[RunResult], spec: StudySpec) -> list[dict[str, object]]:
    """Per world: the oracle verdict plus which manifest policies resolved it."""
    rows: list[dict[str, object]] = []
    for world_index, world in enumerate(spec.worlds):
        row: dict[str, object] = {
            "world_index": world_index,
            "world": "".join(world),
            "oracle_resolvable": oracle_resolvable(world, spec),
        }
        for policy_name in spec.policy_names:
            run = next(
                r for r in results if r.world_index == world_index and r.policy == policy_name
            )
            row[f"resolved_by_{policy_name}"] = run.resolved
        rows.append(row)
    return rows


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def write_results(out_dir: Path, results: list[RunResult], spec: StudySpec) -> None:
    """runs.csv, summary.csv and impossible_views.csv, byte-stable across replays."""
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "runs.csv", RUN_FIELDS, [r.as_row() for r in results])
    write_csv(out_dir / "summary.csv", SUMMARY_FIELDS, summarize(results, spec))
    write_csv(out_dir / "impossible_views.csv", IMPOSSIBLE_FIELDS, impossible_table(results, spec))
