"""Hand-solved tests for the finite world model.

Every expected set/count below was derived by hand from the frozen manifest:
6 bays, openings count exactly 1 or 2 (21 worlds), windows L={0,1,2},
C={1,2,3}, R={3,4,5}, start C, observe=1, move=1, budget=4.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from next_view_selection.model import (
    OPENING,
    SOLID,
    Station,
    belief_entropy,
    enumerate_worlds,
    expected_entropy,
    feasible_actions,
    initial_state,
    load_manifest,
    spec_from_manifest,
    update_belief,
)

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifest.json"


@pytest.fixture(scope="module")
def spec():
    return spec_from_manifest(load_manifest(MANIFEST_PATH))


def world(text: str) -> tuple[str, ...]:
    return tuple(text)


def test_frozen_world_list_matches_its_enumeration_rule(spec):
    assert spec.worlds == enumerate_worlds(spec.n_bays, spec.allowed_counts)
    assert len(spec.worlds) == 21  # C(6,1) + C(6,2) = 6 + 15
    assert spec.worlds[0] == world("OOSSSS")
    assert spec.worlds[-1] == world("SSSSSO")


def test_manifest_counts_block_is_honest(spec):
    manifest = load_manifest(MANIFEST_PATH)
    counts = manifest["counts"]
    assert counts["worlds"] == len(spec.worlds)
    assert counts["observation_actions"] == len(spec.stations) == 3
    undirected = sum(
        1
        for a in spec.stations
        for b in spec.stations
        if abs(a.position - b.position) == 1 and a.position < b.position
    )
    # 4 directed moves: C->L, C->R, L->C, R->C
    assert counts["movement_actions"] == 2 * undirected == 4
    assert counts["max_actions_per_run"] == spec.budget // min(spec.costs.observe, spec.costs.move)


def test_station_windows_and_costs(spec):
    assert [s.window for s in spec.stations] == [(0, 1, 2), (1, 2, 3), (3, 4, 5)]
    assert spec.start_station == "C"
    assert spec.costs.observe == 1 and spec.costs.move == 1 and spec.budget == 4


def test_hand_solved_update_center_sees_opening_middle():
    """Observing C and seeing (O,S,S): bay1 opening, bays 2,3 solid.

    With 1-2 openings total and one already found at bay 1, either it is alone
    or one more hides in {0,4,5}: exactly 4 compatible worlds.
    """
    worlds = enumerate_worlds(6, (1, 2))
    belief = frozenset(worlds)
    station_c = Station("C", 1, (1, 2, 3))
    expected = frozenset(world(w) for w in ("OOSSSS", "SOSSSS", "SOSSOS", "SOSSSO"))
    assert all(w[1] == OPENING and w[2] == SOLID and w[3] == SOLID for w in expected)
    assert update_belief(belief, station_c, ("O", "S", "S")) == expected


def test_hand_solved_update_center_all_solid():
    """Observing C and seeing (S,S,S): no openings in bays 1-3, so 1-2 openings
    hide among {0,4,5}: C(3,1)+C(3,2) = 6 compatible worlds."""
    worlds = enumerate_worlds(6, (1, 2))
    station_c = Station("C", 1, (1, 2, 3))
    posterior = update_belief(frozenset(worlds), station_c, ("S", "S", "S"))
    expected = frozenset(
        world(w) for w in ("OSSSSS", "SSSSOS", "SSSSSO", "OSSSOS", "OSSSSO", "SSSSOO")
    )
    assert posterior == expected
    assert len(posterior) == 6


def test_expected_entropy_hand_values():
    """Hand-checked entropies for the 4-world belief {OOSSSS, SOSSSS, SOSSOS, SOSSSO}."""
    belief = frozenset(world(w) for w in ("OOSSSS", "SOSSSS", "SOSSOS", "SOSSSO"))
    station_l = Station("L", 0, (0, 1, 2))
    station_r = Station("R", 2, (3, 4, 5))
    # L window: bay0=O for 1 world, bay0=S for 3 -> E[H] = 0.75*log2(3)
    assert math.isclose(expected_entropy(belief, station_l), 0.75 * math.log2(3))
    # R window: one 2-world outcome (S,S,S) and two singletons -> E[H] = 0.5
    assert math.isclose(expected_entropy(belief, station_r), 0.5)
    # uniform belief over 4 worlds carries 2 bits
    assert math.isclose(belief_entropy(belief), 2.0)
    # expected entropy reduction: R (1.5 bits) beats L (~0.81 bits)
    assert 2.0 - expected_entropy(belief, station_r) > 2.0 - expected_entropy(belief, station_l)


def test_feasible_actions_at_start(spec):
    state = initial_state(spec)
    assert feasible_actions(state.position, state.budget, spec) == (
        ("observe", "C"),
        ("move", "L"),
        ("move", "R"),
    )
