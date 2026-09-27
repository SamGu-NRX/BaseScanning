"""WS-09 gate producers write engine output, and stay quiet when the laser is absent."""

from __future__ import annotations

import json
from pathlib import Path

from server.eval.producers import main, produce, strip_coverage, veto_pairs


def test_strip_coverage_removes_bands_grid_and_keyframes():
    scene = {
        "coverage": {"bands": [{"id": "b"}], "grid": {"path": "coverage.npz"}, "rule": "footprint3in"},
        "keyframes": [{"id": "k"}],
        "walls": [],
    }
    stripped = strip_coverage(scene)
    assert stripped["coverage"]["bands"] == []
    assert "grid" not in stripped["coverage"]
    assert stripped["coverage"]["rule"] == "footprint3in"
    assert stripped["keyframes"] == []
    assert scene["keyframes"]


def test_veto_pairs_hold_a_downgrade_and_flag_an_added_pass():
    with_c2 = {
        "candidates": [
            {"id": "a", "wall_id": "w1", "start_s_m": [0.0, 1.0], "outcome": "unsure"},
            {"id": "b", "wall_id": "w1", "start_s_m": [2.0, 3.0], "outcome": "pass"},
        ]
    }
    without = {
        "candidates": [
            {"id": "a", "wall_id": "w1", "start_s_m": [0.0, 1.0], "outcome": "pass"},
        ]
    }
    pairs = {item["id"]: item for item in veto_pairs(with_c2, without)}
    assert pairs["a"] == {"id": "a", "with_c2": "unsure", "without_c2": "pass"}
    assert pairs["b"]["with_c2"] == "pass"
    assert pairs["b"]["without_c2"] == "fail"


def test_produce_skips_when_the_laser_is_absent(tmp_path: Path):
    repo = tmp_path / "repo"
    scene = repo / "data" / "fixtures" / "scenes" / "eth3d-facade" / "gt-laser"
    scene.mkdir(parents=True)
    (scene / "scene.json").write_text("{}\n")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    assert produce(repo, inputs, work=tmp_path / "work") == []
    assert not (inputs / "positive").exists()
    assert not (inputs / "safety").exists()


def test_produce_round_trip_uses_the_engine(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    _plant(repo, "eth3d-facade")
    _plant(repo, "eth3d-electro")
    calls: list[tuple] = []

    def fake_eval(scene_dir, out, *, config):
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        calls.append((Path(scene_dir).name, tuple(config.profiles), config.ground_rule, config.c2, config.absent_facts))
        report = {"decidability": {profile: {"share": 0.9} for profile in config.profiles}}
        (out / "criteria_report.json").write_text(json.dumps(report))
        return {
            profile: {
                "site": {"decision": "manual_review"},
                "candidates": [{"id": "a", "wall_id": "w1", "start_s_m": [0.0, 1.0], "outcome": "unsure"}],
                "criteria": [{"id": "wall_backing", "outcome": "unsure"}],
            }
            for profile in config.profiles
        }

    monkeypatch.setattr("server.eval.producers.evaluate_scene", fake_eval)
    monkeypatch.setattr(
        "server.eval.producers.subprocess.run",
        lambda cmd, check: Path(cmd[3]).mkdir(parents=True, exist_ok=True),
    )
    inputs = tmp_path / "inputs"
    written = produce(repo, inputs, work=tmp_path / "work")
    assert (inputs / "criteria" / "eth3d-facade" / "gt-laser" / "criteria_report.json").is_file()
    assert (inputs / "criteria" / "eth3d-electro" / "gt-laser" / "criteria_report.json").is_file()
    assert (inputs / "positive" / "eth3d-facade" / "gt-laser-gravel" / "C" / "footprint3in.json").is_file()
    assert (inputs / "positive" / "eth3d-facade" / "gt-laser-gravel" / "C" / "angle80.json").is_file()
    assert (inputs / "safety" / "no_coverage" / "facade-C-c2.json").is_file()
    assert (inputs / "safety" / "no_coverage" / "facade-C-noc2.json").is_file()
    assert list((inputs / "safety" / "veto").glob("*.json"))
    assert written
    assert ("gt-laser-gravel", ("C",), "angle80", True, "ignore") in calls
    assert ("gt-laser-gravel", ("C",), "footprint3in", True, "ignore") in calls


def test_cli_without_update_leaves_fixtures_alone(tmp_path: Path, monkeypatch):
    seen: dict[str, Path] = {}

    def fake(repo, inputs, work=None):
        seen["inputs"] = Path(inputs)
        return []

    monkeypatch.setattr("server.eval.producers.produce", fake)
    assert main(["--repo", str(tmp_path)]) == 0
    assert seen["inputs"] == tmp_path / "runs" / "gates-offline" / "produced"
    fixtures = tmp_path / "data" / "fixtures"
    assert main(["--repo", str(tmp_path), "--update", "--inputs", str(fixtures)]) == 0
    assert seen["inputs"] == fixtures


def _plant(repo: Path, site: str) -> None:
    scene = repo / "data" / "fixtures" / "scenes" / site / "gt-laser"
    scene.mkdir(parents=True)
    (scene / "scene.json").write_text('{"walls":[{"id":"w1","baseline_m":[[0,0],[1,0]]}]}\n')
    cache = repo / ".fixtures" / "gt" / site / "gt-laser"
    cache.mkdir(parents=True)
    (cache / "laser.ply").write_bytes(b"ply")
    (cache / "occluders.ply").write_bytes(b"ply")
