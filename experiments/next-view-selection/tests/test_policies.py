"""Hand-solved tests for the policies: full traces worked out on paper.

The derivations live in README.md ("Hand-solved traces"); the tests pin them.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from next_view_selection.model import initial_state, load_manifest, spec_from_manifest
from next_view_selection.policies import make_policies
from next_view_selection.study import simulate

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifest.json"


@pytest.fixture(scope="module")
def spec():
    return spec_from_manifest(load_manifest(MANIFEST_PATH))


def run(policy_name: str, world_text: str, spec):
    policies = make_policies(spec)
    world = tuple(world_text)
    return simulate(world, spec.worlds.index(world), policy_name, policies[policy_name], spec)


def answer_of(result) -> str:
    return "".join(result.answer)


def test_uncertainty_reduction_observes_center_first(spec):
    """At the prior (21 worlds) the center window strictly reduces expected
    entropy while any move gains nothing: the first action must be observe:C."""
    policies = make_policies(spec)
    state = initial_state(spec)
    action = policies["uncertainty-reduction"].choose(state, spec, random.Random(0))
    assert action == ("observe", "C")


def test_uncertainty_reduction_full_trace_oossss(spec):
    """World OOSSSS (openings at bays 0,1).

    Hand derivation: observe:C -> (O,S,S) -> 4 compatible worlds
    {OOSSSS, SOSSSS, SOSSOS, SOSSSO}. Re-observing C is zero-gain and skipped;
    the fallback move prefers R (2 unseen bays: 4,5) over L (1 unseen: 0).
    observe:R sees (S,S,S) leaving {OOSSSS, SOSSSS}; observe:R is now
    zero-gain, so the policy walks back to C and the budget dies at 0.
    Ends unresolved with 2 compatible worlds; the lexicographic guess is
    "OOSSSS", which happens to be correct but is unsupported by evidence.
    """
    result = run("uncertainty-reduction", "OOSSSS", spec)
    assert result.actions == ["observe:C", "move:R", "observe:R", "move:C"]
    assert result.cost_used == 4
    assert not result.resolved
    assert result.final_belief_size == 2
    assert answer_of(result) == "OOSSSS"
    assert result.unsupported and result.guess_correct


def test_uncertainty_reduction_resolves_sossos_at_cost_three(spec):
    """World SOSSOS (openings at bays 1,4).

    After observe:C the same 4-world belief holds; the hand-computed expected
    entropy gains are 1.5 bits (observe:R) vs ~0.81 bits (observe:L), so the
    policy walks right and observe:R leaves exactly one compatible world:
    resolved at cost 3, no guess needed.
    """
    result = run("uncertainty-reduction", "SOSSOS", spec)
    assert result.actions == ["observe:C", "move:R", "observe:R"]
    assert result.resolved
    assert answer_of(result) == "SOSSOS"
    assert result.cost_used == 3
    assert not result.unsupported


def test_nearest_unseen_walks_left_on_ties(spec):
    """World OOSSSS: after observe:C both neighbours are 1 step away; the
    left tie-break pays off — observe:L sees both openings and resolves."""
    result = run("nearest-unseen", "OOSSSS", spec)
    assert result.actions == ["observe:C", "move:L", "observe:L"]
    assert result.resolved
    assert answer_of(result) == "OOSSSS"
    assert result.cost_used == 3


def test_nearest_unseen_exhausts_budget_without_resolving_ssssso(spec):
    """World SSSSSO (single opening at bay 5): nearest-unseen covers bays 0-3,
    spends its last unit walking back toward R, and never sees bay 5."""
    result = run("nearest-unseen", "SSSSSO", spec)
    assert result.actions == ["observe:C", "move:L", "observe:L", "move:C"]
    assert result.cost_used == 4
    assert not result.resolved
    assert result.final_belief_size == 3
    assert answer_of(result) == "SSSSOO"  # lexicographic guess among {SSSSOO, SSSSOS, SSSSSO}
    assert result.unsupported and not result.guess_correct


def test_fixed_order_resolves_when_left_window_holds_both_openings(spec):
    """Fixed order L,C,R walks left first; for OOSSSS observe:L alone resolves."""
    result = run("fixed-order", "OOSSSS", spec)
    assert result.actions == ["move:L", "observe:L"]
    assert result.resolved
    assert answer_of(result) == "OOSSSS"
    assert result.cost_used == 2


def test_fixed_order_wastes_its_budget_on_ssssso(spec):
    """Fixed order L,C,R: two observations cost 2 moves + 2 observes = the whole
    budget; bays 4,5 stay unseen and the run ends unresolved."""
    result = run("fixed-order", "SSSSSO", spec)
    assert result.actions == ["move:L", "observe:L", "move:C", "observe:C"]
    assert result.cost_used == 4
    assert not result.resolved
    assert result.final_belief_size == 3
    assert result.unsupported and not result.guess_correct


def test_seeded_random_is_deterministic_per_seed_and_within_budget(spec):
    for world_text in ("OOSSSS", "SOSSOS", "SSSSSO", "SSSOSS"):
        first = run("seeded-random", world_text, spec)
        second = run("seeded-random", world_text, spec)
        assert first.as_row() == second.as_row()
        assert first.cost_used <= spec.budget
