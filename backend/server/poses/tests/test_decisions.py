"""Pure checks: pairs, budget, priors' tracking rule, fallback, bridge, blur, spacing."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pycolmap

from server.common.geometry import arkit_to_colmap_pose, colmap_to_arkit_pose, column_major, from_column_major
from server.poses.blur import blur_px, is_blurred
from server.poses.bridge import closeup_registered, join_accepted
from server.poses.epochs import epoch_jobs
from server.poses.fallback import choice_warnings, choose_model, scale_warning, trigger_details, triggers, use_fixed
from server.poses.fragments import FragmentInfo, choose_primary
from server.poses.packet_io import Frame
from server.poses.pairs import cross_epoch_pairs, guided_pairs
from server.poses.params import PoseParams
from server.poses.run import _mapper_exclusion
from server.poses.spacing import spacing_warnings
from server.poses.thin import heading_deg, select_guided, select_photo


def test_pose_roundtrip():
    rng = np.random.default_rng(0)
    pose = np.eye(4)
    pose[:3, :3] = _rot(rng.normal(size=3))
    pose[:3, 3] = rng.normal(size=3)
    rotation, translation = arkit_to_colmap_pose(pose)
    back = colmap_to_arkit_pose(rotation, translation)
    assert np.allclose(back, pose, atol=1e-9)
    assert np.allclose(from_column_major(column_major(pose)), pose, atol=1e-9)


def _rot(w):
    theta = np.linalg.norm(w)
    if theta < 1e-12:
        return np.eye(3)
    k = w / theta
    x, y, z = k
    cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + np.sin(theta) * cross + (1 - np.cos(theta)) * (cross @ cross)


def test_pairs_stay_inside_one_call_and_respect_radius():
    names = [f"a{i}.jpg" for i in range(6)]
    centers = np.stack([np.array([float(i), 0.0, 0.0]) for i in range(6)])
    params = PoseParams(sequential_overlap=1, spatial_neighbors=2, spatial_radius_m=2.5)
    pairs = guided_pairs(names, centers, params)
    assert ("a0.jpg", "a5.jpg") not in pairs
    assert ("a0.jpg", "a1.jpg") in pairs
    assert all(a < b for a, b in pairs)


def test_no_cross_epoch_when_built_per_epoch():
    params = PoseParams(sequential_overlap=10, spatial_neighbors=40, spatial_radius_m=20)
    e1 = guided_pairs(["a.jpg", "b.jpg"], np.array([[0.0, 0, 0], [1.0, 0, 0]]), params)
    e2 = guided_pairs(["c.jpg", "d.jpg"], np.array([[0.0, 0, 0], [1.0, 0, 0]]), params)
    flat = e1 + e2
    assert not any("a.jpg" in p and "c.jpg" in p for p in [(a, b) for a, b in flat])


def test_cross_epoch_pairs_are_exhaustive_until_the_cap():
    left = [f"a{i:03d}" for i in range(12)]
    right = [f"b{i:03d}" for i in range(12)]
    pairs = cross_epoch_pairs(left, right, overlap=10)
    assert len(pairs) == 144
    assert ("a000", "b000") in pairs
    big_l = [f"a{i:03d}" for i in range(200)]
    big_r = [f"b{i:03d}" for i in range(200)]
    edge = cross_epoch_pairs(big_l, big_r, overlap=10)
    assert len(edge) == 100
    assert ("a199", "b000") in edge
    assert ("a000", "b000") not in edge


def test_frame_budget_keeps_meter_and_caps():
    n = 80
    centers = np.stack([np.array([float(i), 0.0, 0.0]) for i in range(n)])
    headings = [0.0] * n
    chosen = select_guided(n, centers, headings, must=[3], limit=48)
    assert 3 in chosen
    assert len(chosen) == 48
    photo = select_photo(100, must=[1, 2], limit=48)
    assert 1 in photo and 2 in photo
    assert len(photo) <= 48


def test_heading_distinguishes_close_cameras():
    pose = np.eye(4)
    # Identity looks along ARKit −Z, which is yaw ±180 in a y-up frame.
    assert abs(abs(heading_deg(pose)) - 180) < 1e-6
    turned = pose.copy()
    yaw = np.deg2rad(25)
    turned[:3, :3] = np.array([[np.cos(yaw), 0, np.sin(yaw)], [0, 1, 0], [-np.sin(yaw), 0, np.cos(yaw)]])
    assert abs(heading_deg(turned) - heading_deg(pose)) > 20


def test_fallback_triggers():
    params = PoseParams()
    assert triggers(mapper_registered=48, mapper_given=48, rms_m=0.1, scale_pct=1.0, params=params) == []
    assert "sfm_split" in triggers(mapper_registered=40, mapper_given=48, rms_m=0.1, scale_pct=0.0, params=params)
    # 40/48 = 0.833 < 0.90
    assert "sfm_bent" in triggers(mapper_registered=48, mapper_given=48, rms_m=0.26, scale_pct=0.0, params=params)
    assert "sfm_scale_off" in triggers(mapper_registered=48, mapper_given=48, rms_m=0.1, scale_pct=5.1, params=params)
    assert scale_warning(3.1, params) == "scale_vs_arkit_off"
    assert scale_warning(1.0, params) is None
    assert use_fixed(99, 67, ["sfm_split"])
    assert not use_fixed(10, 67, ["sfm_split"])
    assert not use_fixed(99, 67, [])


def test_trigger_details_record_metric_against_threshold():
    params = PoseParams()
    rows = {
        row["code"]: row
        for row in trigger_details(mapper_registered=40, mapper_given=48, rms_m=0.26, scale_pct=-5.1, params=params)
    }
    assert rows["sfm_split"]["fired"] is True
    assert rows["sfm_split"]["value"] == 40 / 48
    assert rows["sfm_split"]["threshold"] == params.fallback_min_registered
    assert rows["sfm_bent"]["value"] == 0.26
    assert rows["sfm_bent"]["threshold"] == params.bent_rms_m
    assert rows["sfm_bent"]["fired"] is True
    assert rows["sfm_scale_off"]["value"] == 5.1
    assert rows["sfm_scale_off"]["threshold"] == params.fallback_max_scale_dev_pct
    quiet = trigger_details(mapper_registered=48, mapper_given=48, rms_m=0.1, scale_pct=1.0, params=params)
    assert [row["code"] for row in quiet if row["fired"]] == []
    assert params.fallback_min_registered == 0.90
    assert params.bent_rms_m == 0.25
    assert params.fallback_max_scale_dev_pct == 5.0
    assert params.select_min_registered == 0.60
    assert params.num_threads == 4


def _choice(**kwargs):
    params = kwargs.pop("params", PoseParams())
    defaults = dict(
        guided=True,
        amended=False,
        frames_given=99,
        prior_model_registered=82,
        mapper_registered=82,
        fixed_registered=None,
        rms_m=0.056,
        scale_pct=-0.146,
        triggers=[],
        params=params,
    )
    defaults.update(kwargs)
    return choose_model(**defaults)


def test_prior_ships_at_60_percent_with_carried_frames():
    # 2022-02-10 s0 prior: 82/99. The logged split is 82/99 < 0.90 and does not swap.
    choice = _choice()
    assert choice["shipped"] == "arkit_prior"
    assert choice["action"] == "keep"
    assert choice["swap_reasons"] == []
    assert "82/99" in choice["reason"]
    assert "60%" in choice["reason"]
    # A6 historical 67/99 = 0.68 stays on the prior even if fixed covers every frame.
    historical = _choice(prior_model_registered=67, mapper_registered=48, fixed_registered=99)
    assert historical["shipped"] == "arkit_prior"
    assert historical["action"] == "keep"
    assert "67/99" in historical["reason"]


def test_prior_ships_when_only_the_arkit_bend_fires():
    # Facade prior: 74/76, camera-vs-ARKit RMS 0.329 m. Fixed covers 76 frames.
    # The mapper subset was 46; that count must not decide the swap.
    choice = _choice(
        frames_given=76,
        prior_model_registered=74,
        mapper_registered=46,
        fixed_registered=76,
        rms_m=0.329,
        scale_pct=-0.395,
        mean_reproj_px=0.4,
    )
    assert choice["shipped"] == "arkit_prior"
    assert choice["action"] == "keep"
    assert choice["swap_reasons"] == []
    assert choice["arkit_disagree"] is True
    assert "74/76" in choice["reason"]
    assert "sfm_arkit_disagree" in choice["reason"]
    assert choice_warnings(choice) == ["sfm_arkit_disagree"]


def test_fallback_only_on_scale_share_or_no_model():
    short = _choice(
        frames_given=40,
        prior_model_registered=7,
        mapper_registered=7,
        rms_m=0.06,
        scale_pct=1.0,
        fixed_registered=40,
    )
    assert short["action"] == "replace"
    assert short["shipped"] == "arkit_fixed"
    assert "under 60%" in short["reason"]
    assert "sfm_registered_low" in choice_warnings(short)
    bent = _choice(prior_model_registered=90, rms_m=1.48, scale_pct=0.0, fixed_registered=99)
    assert bent["shipped"] == "arkit_prior"
    assert bent["action"] == "keep"
    assert "sfm_arkit_disagree" in bent["reason"]
    scaled = _choice(prior_model_registered=90, rms_m=0.10, scale_pct=6.0, fixed_registered=99)
    assert scaled["shipped"] == "arkit_fixed"
    assert "over 5.0%" in scaled["reason"]
    assert "sfm_scale_off" in choice_warnings(scaled)
    weak_fixed = _choice(
        frames_given=40,
        prior_model_registered=7,
        rms_m=0.06,
        scale_pct=1.0,
        fixed_registered=4,
    )
    assert weak_fixed["shipped"] == "arkit_prior"
    assert "not more than the prior's 7" in weak_fixed["reason"]
    pending = _choice(
        frames_given=40,
        prior_model_registered=7,
        rms_m=0.06,
        scale_pct=1.0,
        fixed_registered=None,
    )
    assert pending["action"] == "try_fixed"
    missing = _choice(prior_model_registered=None, mapper_registered=0, fixed_registered=4, frames_given=4)
    assert missing["shipped"] == "arkit_fixed"
    assert missing["swap_reasons"] == ["no_model"]
    assert choice_warnings(missing) == ["sfm_no_model"]


def test_fragment_meter_beats_larger():
    big = FragmentInfo("f0", ["a"], registered=50, points3d=10, has_meter=False)
    small = FragmentInfo("f1", ["b"], registered=12, points3d=10, has_meter=True)
    primary, status = choose_primary([big, small])
    assert primary is small
    assert status == "ok"
    assert small.primary and not big.primary
    none_m, status = choose_primary([big])
    assert none_m is big and status == "no_meter_model"


def test_bridge_and_join():
    params = PoseParams()
    assert closeup_registered(in_primary=True, points3d=30, reproj_px=2.0, params=params)
    assert not closeup_registered(in_primary=True, points3d=29, reproj_px=0.5, params=params)
    assert not closeup_registered(in_primary=False, points3d=100, reproj_px=0.2, params=params)
    assert join_accepted(n_registered=3, mean_reproj_px=2.0, rms_m=0.10)
    assert not join_accepted(n_registered=3, mean_reproj_px=2.0, rms_m=0.11)


def test_blur_and_spacing():
    params = PoseParams()
    # 1/60 s, 10 deg/s, f=1500 px → about 4.4 px, over the 4 px cut.
    assert blur_px(1 / 60, np.deg2rad(10), 1500) > 4
    assert is_blurred(4.5, "imu", params)
    assert not is_blurred(3.0, "imu", params)
    assert is_blurred(1.0, "laplacian", params)
    # §8.1: 40 frames thinned to 1 m steps along a walk longer than 20 m.
    thinned = np.stack([np.array([i * 1.0, 0.0, 0.0]) for i in range(40)])
    assert "photo_spacing_sparse" in spacing_warnings(thinned, registered=40, given=40, n_models=1, params=params)
    tight = np.stack([np.array([i * 0.4, 0.0, 0.0]) for i in range(40)])
    assert "photo_spacing_sparse" not in spacing_warnings(tight, registered=40, given=40, n_models=1, params=params)
    assert "photo_spacing_sparse" in spacing_warnings(tight, registered=40, given=40, n_models=2, params=params)
    assert "photo_spacing_sparse" in spacing_warnings(None, registered=10, given=40, n_models=1, params=params)
    assert "photo_spacing_sparse" not in spacing_warnings(
        thinned, registered=40, given=40, n_models=1, params=params, metric_scale=False
    )


def _frame(fid: str, flags: tuple[str, ...] = (), *, meter: bool = False) -> Frame:
    return Frame(
        id=fid,
        name=f"{fid}.jpg",
        src=Path(f"{fid}.jpg"),
        w=4,
        h=4,
        fx=1,
        fy=1,
        cx=0,
        cy=0,
        pose=np.eye(4),
        timestamp=0,
        tracking="normal",
        epoch="e1",
        purpose=None,
        kind="keyframe",
        exposure_s=None,
        intrinsics_source="arkit",
        intrinsics_assumed=False,
        has_depth=False,
        meter=meter,
        flags=flags,
    )


def test_epoch_jobs_do_not_mix_large_epochs():
    frames = [
        _frame("a0"),
        _frame("a1"),
        _frame("a2"),
        _frame("b0"),
        _frame("b1"),
        _frame("b2"),
    ]
    frames[0].epoch = frames[1].epoch = frames[2].epoch = "e1"
    frames[3].epoch = frames[4].epoch = frames[5].epoch = "e2"
    jobs = epoch_jobs(frames, meter_ids=["a0"], params=PoseParams())
    names = [{frame.id for frame in job.frames} for job in jobs]
    assert {"a0", "a1", "a2"} in names
    assert {"b0", "b1", "b2"} in names


def test_quality_filter_keeps_meter_and_drops_flagged():
    from server.poses.packet_io import PacketView

    frames = [
        _frame("k0", ("motion_blur",)),
        _frame("k1", ("high_iso",), meter=True),
        _frame("k2"),
    ]
    view = PacketView("p", "arkit", Path("."), frames, "guided", "image", "e1", "tap")
    excluded, warnings = _mapper_exclusion(frames, view, PoseParams())
    assert excluded == {"k0"}
    assert any(w.startswith("meter_frame_degraded:k1") for w in warnings)


def test_pycolmap_420_can_express_the_prior():
    assert pycolmap.__version__.split(".")[0] == "4"
    assert hasattr(pycolmap.PosePrior(), "corr_data_id")
    options = pycolmap.IncrementalPipelineOptions()
    assert hasattr(options, "use_prior_position")
    assert options.use_prior_position is False
