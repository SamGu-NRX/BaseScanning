"""Study-level invariants, oracle consistency, and end-to-end determinism."""

from __future__ import annotations

from pathlib import Path

import pytest

from next_view_selection.figures import write_figures
from next_view_selection.model import load_manifest, spec_from_manifest
from next_view_selection.study import (
    impossible_table,
    oracle_resolvable,
    run_study,
    summarize,
    write_results,
)

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifest.json"


@pytest.fixture(scope="module")
def spec():
    return spec_from_manifest(load_manifest(MANIFEST_PATH))


@pytest.fixture(scope="module")
def results(spec):
    return run_study(spec)


def test_run_grid_is_complete(results, spec):
    assert len(results) == len(spec.worlds) * len(spec.policy_names) == 84
    assert {r.policy for r in results} == set(spec.policy_names)


def test_budget_and_resolution_invariants(results, spec):
    for run in results:
        assert run.cost_used <= spec.budget
        assert run.seed == spec.base_seed + run.world_index
        if run.resolved:
            assert run.final_belief_size == 1
            assert run.answer == run.world
            assert run.guess_correct
            assert not run.unsupported
        else:
            assert run.final_belief_size > 1
            assert run.unsupported
            assert run.answer in spec.worlds  # a forced guess is still a real world


def test_unsupported_answers_never_masquerade_as_supported(results):
    assert all(not (run.unsupported and run.resolved) for run in results)


def test_oracle_admits_every_policy_resolution(results, spec):
    """Any world a policy actually resolved must be oracle-resolvable."""
    for run in results:
        if run.resolved:
            assert oracle_resolvable(run.world, spec), run.world


def test_hand_proven_impossible_view(spec):
    """World SSSSSO (single opening at bay 5) is impossible within budget 4.

    Hand proof: at most two observations fit (any two non-central windows need
    4 moves; only 4 budget units exist). The reachable window pairs are
    {C,L} -> bays 0-3, leaving {4,5} with 1-2 openings (3 worlds), or
    {C,R} -> bays 1-5, leaving bay 0 free (2 worlds). No singleton, ever.
    """
    assert not oracle_resolvable(tuple("SSSSSO"), spec)
    # and its mirror: a single opening at bay 0 IS resolvable (C then R pins it,
    # because a second opening has no unseen bay left to hide in)
    assert oracle_resolvable(tuple("OSSSSS"), spec)


def test_summary_matches_runs(results, spec):
    rows = summarize(results, spec)
    assert [row["policy"] for row in rows] == list(spec.policy_names)
    for row in rows:
        runs = [r for r in results if r.policy == row["policy"]]
        assert row["runs"] == len(runs) == 21
        assert row["resolved"] == sum(1 for r in runs if r.resolved)
        assert row["unsupported"] == sum(1 for r in runs if r.unsupported)
        assert pytest.approx(row["resolution_rate"], abs=1e-9) == row["resolved"] / 21


def test_end_to_end_outputs_are_byte_deterministic(spec, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    for out in (first, second):
        results = run_study(spec)
        write_results(out, results, spec)
        write_figures(summarize(results, spec), out / "figures")
    for name in ("runs.csv", "summary.csv", "impossible_views.csv"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
    for name in ("fig_resolution.png", "fig_cost.png", "fig_unsupported.png"):
        assert (first / "figures" / name).read_bytes() == (
            second / "figures" / name
        ).read_bytes(), name


def test_impossible_table_agrees_with_runs_and_oracle(results, spec):
    for row in impossible_table(results, spec):
        world = tuple(str(row["world"]))
        assert row["oracle_resolvable"] == oracle_resolvable(world, spec)
        for policy_name in spec.policy_names:
            resolved_by = row[f"resolved_by_{policy_name}"]
            run = next(
                r
                for r in results
                if r.world_index == row["world_index"] and r.policy == policy_name
            )
            assert resolved_by == run.resolved
            if resolved_by:
                assert row["oracle_resolvable"]
