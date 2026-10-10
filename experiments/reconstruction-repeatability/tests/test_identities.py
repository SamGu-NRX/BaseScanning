"""Pipeline-level witnesses: identities, guarded history, and the history-dependent wall."""

from __future__ import annotations

import json

import bundles
import compare

ORDER_TOL_FT = 0.005  # manifest tolerances.order_ft


def run(bundle, out, work, pipeline_run):
    pipeline_run(bundle, out, work)
    return compare.collect_outputs(out)


def test_repeat_runs_are_byte_identical(tmp_path, pipeline_run):
    """The floor: two fresh runs on the same bundle ship the same outputs."""
    bundle = bundles.write_bundle(tmp_path / "bundle")
    o1 = run(bundle, tmp_path / "out1", tmp_path / "work1", pipeline_run)
    o2 = run(bundle, tmp_path / "out2", tmp_path / "work2", pipeline_run)
    assert compare.outputs_equal(o1, o2)


def test_warm_cache_is_byte_identical(tmp_path, pipeline_run):
    """The second run in one work directory reads the cache and changes nothing."""
    bundle = bundles.write_bundle(tmp_path / "bundle")
    o1 = run(bundle, tmp_path / "out1", tmp_path / "work", pipeline_run)
    o2 = run(bundle, tmp_path / "out2", tmp_path / "work", pipeline_run)
    assert compare.outputs_equal(o1, o2)


def test_frames_reordered_wall_moves_within_tolerance(tmp_path, pipeline_run):
    """Keyframe order is not a declared input; the fusion loop sums in list order, so the wall may
    move only by float noise."""
    bundle = bundles.write_bundle(tmp_path / "bundle")
    reversed_ = bundles.write_bundle(tmp_path / "bundle-rev", reverse=True)
    o1 = run(bundle, tmp_path / "out1", tmp_path / "work1", pipeline_run)
    o2 = run(reversed_, tmp_path / "out2", tmp_path / "work2", pipeline_run)
    assert compare.json_diff_ft(o1, o2) <= ORDER_TOL_FT


def test_same_size_cache_edit_moves_the_wall(tmp_path, pipeline_run):
    """The history-dependent wall, as a pipeline witness: declared inputs identical, cache history
    different, wall different. No guard catches a same-size edit (test_keying.py witnesses the
    gap); this records how far the wall moves when one lands."""
    bundle = bundles.write_bundle(tmp_path / "bundle")
    work = tmp_path / "work"
    baseline = run(bundle, tmp_path / "out1", work, pipeline_run)

    bundles.poison_cache(work, "k1")
    poisoned = run(bundle, tmp_path / "out2", work, pipeline_run)

    assert compare.json_diff_ft(baseline, poisoned) > 0.01
    # The wrong answer is stable: naive run-twice repeatability would pass it.
    again = run(bundle, tmp_path / "out3", work, pipeline_run)
    assert compare.outputs_equal(poisoned, again)


def test_baseline_reconstructs_the_planes(tmp_path, pipeline_run):
    """The synthetic room reconstructs sanely: the fitted wall runs along the room's x axis at
    z = 0 (the stub's wall is infinite in x, so the fitted stretch spans the volume bounds), and
    scene.json was rebuilt on top of the bundle's own scene."""
    bundle = bundles.write_bundle(tmp_path / "bundle")
    out = run(bundle, tmp_path / "out", tmp_path / "work", pipeline_run)
    geo = json.loads(out["geometry.json"])
    wall = geo["walls"][0]
    base = wall["baseline"]
    assert all(abs(p[1]) < 0.05 for p in base), f"wall not near z = 0: {base}"
    along = wall["along"]
    assert abs(along[0]) > 0.99 and abs(along[1]) < 0.01, f"wall not along x: {along}"
    lo, hi = wall["s_range_ft"]
    assert hi - lo > 20, f"fitted stretch implausibly short: {wall['s_range_ft']}"
    assert json.loads(out["scene.json"])["meter"]["wall_id"] == "wall"
