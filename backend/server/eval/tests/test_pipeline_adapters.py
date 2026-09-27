"""SD-84: a missing stage SKIPs as not_merged. A present stage is the owner's API."""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest

from server.eval.adapters.criteria import evaluate_scene
from server.eval.gates import _decidability_rows
from server.eval.lamar import available
from server.eval.pipeline_adapters import criteria_engine, poses_run
from server.eval.tests.flat_wall import write


def test_criteria_adapter_uses_ws09(tmp_path: Path):
    """criteria_engine() is the only way in. The report still has decidability.<profile>.share."""
    config_cls, evaluate = criteria_engine()
    assert config_cls.__module__ == "server.criteria.config"
    assert config_cls.__name__ == "CriteriaConfig"
    assert evaluate.__module__ == "server.criteria.run"
    assert evaluate.__name__ == "evaluate_scene"
    assert Path("server/criteria/run.py").is_file()
    with pytest.raises(Exception, match="^scene_invalid$"):
        evaluate_scene(tmp_path, tmp_path / "missing")

    scene = write(tmp_path / "wall")
    out = tmp_path / "out"
    produced = evaluate_scene(
        scene,
        out,
        config=config_cls(profiles=("C",), ground_rule="angle80", c2=False, c3=False),
    )
    assert produced["C"]["evaluator"]["path"] == "C1-rules-3d+hs11-chain"
    assert produced["C"]["evaluator"]["rules_profile"] == "C"
    report = json.loads((out / "criteria_report.json").read_text())
    share = report["decidability"]["C"]["share"]
    assert isinstance(share, float)
    copied = tmp_path / "criteria" / "eth3d-facade" / "gt-laser" / "criteria_report.json"
    copied.parent.mkdir(parents=True)
    copied.write_text((out / "criteria_report.json").read_text())
    rows = _decidability_rows({"decidability": {"gt_laser_min": {"eth3d-facade": 0.38}}}, tmp_path)
    row = next(item for item in rows if item["id"] == "decidability/eth3d-facade/gt-laser/C")
    assert row["metric"] == "decidability.C.share"
    assert row["value"] == share
    assert row["status"] == ("PASS" if share >= 0.38 else "FAIL")


def test_missing_stage_still_skips_with_not_merged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """SD-84: a blocked stage import still SKIPs. Poses is on this tree, so the live call is run_poses."""
    real_import = builtins.__import__

    def blocked(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"server.criteria", "server.poses"}:
            raise ImportError("blocked")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(ImportError, match="not_merged:ws-09"):
        criteria_engine()
    with pytest.raises(ImportError, match="not_merged:ws-09"):
        evaluate_scene(tmp_path, tmp_path / "out")
    with pytest.raises(ImportError, match="not_merged:ws-04"):
        poses_run()
    assert available(tmp_path) == "not_merged:ws-04"

    monkeypatch.undo()
    run = poses_run()
    assert run.__module__ == "server.poses.run"
    assert run.__name__ == "run_poses"
    assert Path("server/poses/run.py").is_file()
    assert available(tmp_path) is None
