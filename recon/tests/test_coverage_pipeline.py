"""Pipeline-level witnesses for the coverage-extent change (issues #95 and #96).

Each witness runs `pipeline.run` - the caller a real bundle takes - on a synthetic bundle
rendered by `witness_bundle` from analytic planes, with LiDAR depth and no server: no real home
capture, no private rule, no model process. The oracle is what the pipeline writes, never a
test-local copy of the clipping logic: spans are judged against the fitted extent the pipeline
itself reports in geometry.json, ground observation against depth the renderer put on a sloping
plane, and refusals on a short or empty wall track against the errors the pipeline raises.
"""

import json
from pathlib import Path

import pytest
from witness_bundle import FEET, write_flat_room

from recon import pipeline


def run_bundle(bundle: Path, tmp_path: Path) -> dict:
    out = tmp_path / "out"
    pipeline.run(bundle, out, tmp_path / "work", "lidar", None, False)
    return {
        "bundle": bundle,
        "scene": json.loads((out / "scene.json").read_text()),
        "coverage": json.loads((out / "coverage.json").read_text()),
        "geo": json.loads((out / "geometry.json").read_text()),
    }


def test_observed_stops_at_the_fitted_extent_when_the_baseline_runs_past_it(tmp_path):
    # The phone's baseline claims wall from -0.6 to 3.2 m, but the reconstruction only supports
    # 0.1 to 2.6 m: no exported observation may run past the fitted extent on either side. The
    # phone's claim lives in the input bundle; geometry.json reports the fitted baseline and an
    # s_range_ft measured from the meter (metres along x, here 1.0).
    meter_x = 1.0
    r = run_bundle(write_flat_room(tmp_path / "bundle", meter_x=meter_x), tmp_path)
    lo, hi = r["geo"]["walls"][0]["s_range_ft"]
    bundle_doc = json.loads((r["bundle"] / "scene.json").read_text())
    phone = [p[0] for p in bundle_doc["walls"][0]["baseline"]]
    phone_s = [p - meter_x / FEET for p in phone]  # phone baseline into s, feet
    assert min(phone_s) < lo - 0.5 and max(phone_s) > hi + 0.5, (
        "precondition: the baseline claims wall the fit never established"
    )
    entries = r["scene"]["coverage"]["observed"]
    assert any(e["band"] == "wall" for e in entries)
    for e in entries:
        assert e["span_ft"][0] >= lo - 2e-3, e
        assert e["span_ft"][1] <= hi + 2e-3, e
    cov = r["coverage"]
    assert cov["cells_s_ft"][0] == lo and cov["cells_end_ft"][-1] == hi
    widths = [e - s for s, e in zip(cov["cells_s_ft"], cov["cells_end_ft"], strict=True)]
    assert max(widths) == pytest.approx(0.5, abs=2e-3)  # interior cells stay half-foot
    assert min(widths) < 0.5  # an unaligned fit end clips its boundary cell


def test_a_hole_in_the_wall_stays_unobserved_through_the_pipeline(tmp_path):
    # A 0.8 m opening: the fit bridges it (one wall line, GAP_M), but the depth test sees through
    # to the wall behind, so the cells across the hole stay unobserved while their neighbours do
    # not - and the exported spans agree.
    hole = (1.2, 2.0)  # metres along the wall from its left end
    b = write_flat_room(
        tmp_path / "bundle",
        wall_x=(0.0, 3.2),
        baseline_x=(0.0, 3.2),
        xs=(0.2, 0.75, 1.3, 1.85, 2.4, 2.95),
        hole=hole,
        meter_x=0.5,
    )
    r = run_bundle(b, tmp_path)
    lo, hi = r["geo"]["walls"][0]["s_range_ft"]
    assert hi - lo > 8.5, "precondition: the fit bridges the hole, so its cells were computed"
    hole_lo, hole_hi = (hole[0] - 0.5) / FEET, (hole[1] - 0.5) / FEET  # s of the hole, in feet
    cov = r["coverage"]
    centres = [
        (s + e) / 2
        for s, e, ok in zip(
            cov["cells_s_ft"], cov["cells_end_ft"], cov["wall_observed"], strict=True
        )
        if ok
    ]
    assert not any(hole_lo + 0.4 < c < hole_hi - 0.4 for c in centres), centres
    assert any(lo < c < hole_lo - 0.2 for c in centres), centres
    assert any(hole_hi + 0.2 < c < hi for c in centres), centres
    for e in r["scene"]["coverage"]["observed"]:
        if e["band"] == "wall":
            assert e["span_ft"][1] < hole_lo + 0.4 or e["span_ft"][0] > hole_hi - 0.4, e


def test_ground_observed_on_a_falling_fitted_plane(tmp_path):
    # A floor falling 0.35 m per metre out: at 2 ft out the surface is 0.21 m below the meter's
    # plane, past the 10 cm tolerance, so the old horizontal-plane sampler cannot produce a
    # 2 ft ground entry here at all. Samples that follow the fitted plane can.
    fall = 0.35
    b = write_flat_room(
        tmp_path / "bundle",
        fall=fall,
        k=(200.0, 200.0, 320.0, 240.0),  # wide, to reach the floor
    )
    r = run_bundle(b, tmp_path)
    lo, hi = r["geo"]["walls"][0]["s_range_ft"]
    ground = [e for e in r["scene"]["coverage"]["observed"] if e["band"] == "ground"]
    assert ground, "the fitted ground is in, and the depth test saw the floor along it"
    assert max(e["out_ft"] for e in ground) >= 2.0
    for e in ground:
        assert e["span_ft"][0] >= lo - 2e-3 and e["span_ft"][1] <= hi + 2e-3, e
    outs = [o for o in r["coverage"]["ground_out_ft"] if o is not None]
    assert max(outs) >= 0.61  # 2 ft, in metres: seen contiguously from the foot


def test_a_short_wall_track_refuses_instead_of_reporting_coverage(tmp_path):
    # A 0.6 m wall: below the 1.5 m minimum stretch, so there is no line to fit and the pipeline
    # refuses rather than silently exporting coverage of a wall it never established.
    b = write_flat_room(
        tmp_path / "bundle",
        wall_x=(0.8, 1.4),
        baseline_x=(0.8, 1.4),
        xs=(0.7, 1.0, 1.3),
        behind=False,
    )
    with pytest.raises(RuntimeError, match="no straight vertical wall"):
        run_bundle(b, tmp_path)


def test_an_empty_wall_track_refuses(tmp_path):
    # A floor and nothing vertical: no wall line, and the pipeline refuses the same way.
    b = write_flat_room(tmp_path / "bundle", wall_x=None, baseline_x=(0.0, 2.0), behind=False)
    with pytest.raises(RuntimeError, match="no straight vertical wall"):
        run_bundle(b, tmp_path)


def test_two_frames_too_close_together_observe_no_wall_or_ground(tmp_path):
    # Two frames 5 cm apart: under the 0.25 m two-position bar, so nothing wall or ground is
    # exported even though every sample was in view. Free space keeps its own, weaker bar
    # (a known defect), so a facing entry can still appear; that is what the pipeline does.
    b = write_flat_room(tmp_path / "bundle", xs=(1.0, 1.05))
    r = run_bundle(b, tmp_path)
    assert not [e for e in r["scene"]["coverage"]["observed"] if e["band"] in ("wall", "ground")]
    assert len(r["coverage"]["cells_s_ft"]) > 0  # the track ran; it observed nothing two-view


def test_a_bundle_with_no_keyframes_refuses(tmp_path):
    b = write_flat_room(tmp_path / "bundle", xs=())
    with pytest.raises(ValueError, match="no keyframes"):
        run_bundle(b, tmp_path)


def test_one_lidar_frame_cannot_drive_coverage(tmp_path):
    b = write_flat_room(tmp_path / "bundle", xs=(1.0,))
    with pytest.raises(ValueError, match="carry LiDAR"):
        run_bundle(b, tmp_path)
