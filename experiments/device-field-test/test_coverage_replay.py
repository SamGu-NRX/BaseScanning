import json
import sys

import numpy as np
import pytest

import coverage_replay as replay


def test_covered_only_reach_differs_from_skipped_app_cells():
    # GuidancePlanner.reach at ff95f1c accepts both covered and skipped bands.
    levels = {0: ("covered", "covered"), 1: ("skipped", "covered"), 2: ("covered", "covered")}
    covered = {i for i, bands in levels.items() if all(level == "covered" for level in bands)}
    app_done = {i for i, bands in levels.items() if all(level in ("covered", "skipped") for level in bands)}
    assert replay.reach(covered, 1) == .5
    assert replay.reach(app_done, 1) == 1.5


def test_synthetic_replay_left_extent_is_configurable(tmp_path, monkeypatch, capsys):
    pose = np.eye(4)
    angle = np.radians(-35)
    pose[:3, :3] = [[1, 0, 0], [0, np.cos(angle), -np.sin(angle)], [0, np.sin(angle), np.cos(angle)]]
    frames = []
    for i, x in enumerate([-1, -.6]):
        pose[:3, 3] = np.array([x, 2.5, 3]) / replay.FT
        frames.append({"id": f"k{i}", "pose": pose.T.ravel().tolist(), "intrinsics": [250, 250, 600, 600], "w": 1200, "h": 1200})
    scene = tmp_path / "scene.json"
    scene.write_text(json.dumps({"walls": [{"baseline": [[0., 0.], [10., 0.]]}], "meter": {"pos": [0, 3, 0]}, "keyframes": frames}))
    monkeypatch.setattr(sys, "argv", ["coverage_replay.py", str(scene), "--cells", "8", "--left-cells", "8"])
    replay.main()
    last_photo = [line for line in capsys.readouterr().out.splitlines() if line.startswith("k1")][0]
    assert float(last_photo.split()[2]) == 4.0
    monkeypatch.setattr(sys, "argv", ["coverage_replay.py", str(scene), "--left-cells", "3"])
    replay.main()
    last_photo = [line for line in capsys.readouterr().out.splitlines() if line.startswith("k1")][0]
    assert float(last_photo.split()[2]) == 1.5
