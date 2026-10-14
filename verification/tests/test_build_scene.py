"""The ETH3D facade scene builder replaces only its own outputs."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scenes" / "eth3d-facade" / "build_scene.py"
spec = importlib.util.spec_from_file_location("build_scene", SCRIPT)
build_scene = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = build_scene  # dataclasses look their module up there
spec.loader.exec_module(build_scene)


def test_earlier_outputs_are_replaced(tmp_path):
    (tmp_path / "bundle").mkdir()
    (tmp_path / "bundle" / "k00001.jpg").write_bytes(b"old")
    (tmp_path / "scene.json").write_text("{}")
    (tmp_path / "overlays").mkdir()
    build_scene.clear_outputs(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_folder_with_other_files_is_refused_and_left_alone(tmp_path):
    (tmp_path / "scene.json").write_text("{}")
    (tmp_path / "datasets").mkdir()
    (tmp_path / "notes.txt").write_text("keep me")
    with pytest.raises(SystemExit, match=r"did not write \(datasets, notes\.txt\)"):
        build_scene.clear_outputs(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["datasets", "notes.txt", "scene.json"]


def test_a_missing_folder_is_fine_and_a_file_is_refused(tmp_path):
    build_scene.clear_outputs(tmp_path / "new")
    target = tmp_path / "a-file"
    target.write_text("x")
    with pytest.raises(SystemExit, match="is a file"):
        build_scene.clear_outputs(target)
    assert target.read_text() == "x"


def test_a_symlinked_output_is_unlinked_not_followed(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious").write_text("keep")
    out = tmp_path / "out"
    out.mkdir()
    (out / "bundle").symlink_to(elsewhere, target_is_directory=True)
    build_scene.clear_outputs(out)
    assert (elsewhere / "precious").read_text() == "keep"
    assert not (out / "bundle").exists()


def wall(wid: str, s0: float, length: float):
    return build_scene.Wall(wid, np.array([s0, 0.0]), np.array([s0 + length, 0.0]), s0=s0)


def seen_everywhere(kf, w, X, walls):
    return True


@pytest.mark.parametrize(
    ("band", "out_ft"),
    [
        ("wall", 3.29),  # up to the battery's height, not the headroom an omitted view claims
        ("ground", build_scene.GROUND_OUT_FT),
        ("overhead", build_scene.HEADROOM_FT),  # not clear all the way up
    ],
)
def test_every_band_claims_only_the_reach_its_probes_showed(band, out_ft):
    entries, spans = build_scene.band_coverage(
        band, [wall("w", 0.0, 2.0)], [], ["kf"], seen_everywhere
    )
    assert entries == [{"band": band, "span_ft": [0.0, 2.0], "out_ft": out_ft}]
    assert spans == [[0.0, 2.0]]


def test_facing_claims_the_smallest_depth_probed_and_nothing_past_20_ft():
    walls = [wall("a", 0.0, 1.0), wall("b", 1.0, 1.0)]  # meet at a corner: one merged entry
    facing = [("a", 0.0, 1.0, 35.0), ("b", 1.0, 1.5, 12.345), ("b", 1.5, 2.0, 30.0)]
    entries, _ = build_scene.band_coverage("facing", walls, facing, ["kf"], seen_everywhere)
    assert entries == [{"band": "facing", "span_ft": [0.0, 2.0], "out_ft": 12.34}]
    entries, _ = build_scene.band_coverage("facing", walls[:1], facing, ["kf"], seen_everywhere)
    assert entries[0]["out_ft"] == 20.0


def test_an_unseen_probe_leaves_the_stretch_out():
    def only_low(kf, w, X, walls):
        return X[1] <= 1.0  # the top of the wall probe is never seen

    entries, _ = build_scene.band_coverage("wall", [wall("w", 0.0, 2.0)], [], ["kf"], only_low)
    assert entries == []


@pytest.mark.parametrize("length", [2.2, 2.3, 0.05])
def test_non_grid_wall_has_no_excess_or_missing_tail(length):
    entries, spans = build_scene.band_coverage(
        "wall", [wall("w", 0.0, length)], [], ["kf"], seen_everywhere
    )
    assert entries[0]["span_ft"] == [0.0, length]
    assert spans == [[0.0, length]]


@pytest.mark.parametrize("length", [2.2, 2.3, 0.05])
def test_clipped_tail_probes_stay_within_the_wall(length):
    probes = []

    def within_wall(kf, w, point, walls):
        probes.append(float(point[0]))
        return 0.0 <= point[0] <= length

    entries, spans = build_scene.band_coverage(
        "wall", [wall("w", 0.0, length)], [], ["kf"], within_wall
    )
    assert probes and all(0.0 <= p <= length for p in probes)
    assert spans == [[0.0, length]]
    assert entries[0]["span_ft"] == [0.0, length]
