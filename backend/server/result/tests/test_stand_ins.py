"""The result names which stages ran as stand-ins.

The orchestration leaves one impl/<stage>.json per run stage in the result
stage's inputs (schema hss-stage-impl/v1). The composer turns those markers
into a stageImplementations record and a sorted standIns list, refuses a
marker that does not carry the schema, and leaves result.json untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from server.contracts.jobs import CaptureRecord, RunRecord, StageName
from server.result.compose import ResultError, compose_result
from server.result.config import ResultConfig
from server.result.copy import load_catalogue
from server.result.tests.support import DATA

IMPL_SCHEMA = "hss-stage-impl/v1"


def _capture_run():
    capture = json.loads((DATA / "capture.sample.json").read_text())
    epochs = list(capture.pop("epochs"))
    return capture, json.loads((DATA / "run.ok.json").read_text()), epochs


def _compose(out_dir: Path, impl_dir: Path | None):
    capture_raw, run_raw, epochs = _capture_run()
    return compose_result(
        CaptureRecord.model_validate(capture_raw),
        RunRecord.model_validate(run_raw),
        DATA / "synthetic",
        DATA / "synthetic" / "verdicts",
        out_dir,
        reads=DATA / "reads.auto_fill.json",
        scale=None,
        catalogue=load_catalogue(DATA / "prompts.en-US.json"),
        config=ResultConfig(catalogue_path=DATA / "prompts.en-US.json"),
        epochs=epochs,
        impl_dir=impl_dir,
    )


def _marker(stage: str, implementation: str) -> dict:
    callable_ = "server.orchestrator.stubs" if implementation == "stand-in" else "server"
    return {"schema": IMPL_SCHEMA, "stage": stage, "implementation": implementation, "callable": f"{callable_}.{stage}.run"}


def _write_impl_dir(root: Path, implementations: dict[str, str]) -> Path:
    impl_dir = root / "impl"
    impl_dir.mkdir(parents=True)
    for stage, implementation in implementations.items():
        (impl_dir / f"{stage}.json").write_text(json.dumps(_marker(stage, implementation)) + "\n")
    return impl_dir


def _non_result_stages() -> list[str]:
    return [stage for stage in get_args(StageName) if stage != "result"]


def test_every_stage_real_when_all_markers_say_so(tmp_path: Path):
    impl_dir = _write_impl_dir(tmp_path, {stage: "real" for stage in _non_result_stages()})
    composed = _compose(tmp_path / "out", impl_dir)
    internal = composed.internal
    assert internal["stageImplementations"] == {stage: "real" for stage in get_args(StageName)}
    assert internal["standIns"] == []


def test_two_stand_ins_are_named_in_the_record_and_the_page(tmp_path: Path):
    implementations = {stage: "real" for stage in _non_result_stages()}
    implementations["objects"] = "stand-in"
    implementations["reads"] = "stand-in"
    impl_dir = _write_impl_dir(tmp_path, implementations)
    composed = _compose(tmp_path / "out", impl_dir)
    internal = composed.internal
    assert internal["standIns"] == ["objects", "reads"]
    assert internal["stageImplementations"]["objects"] == "stand-in"
    assert internal["stageImplementations"]["reads"] == "stand-in"
    # The result stage counts itself as real, marker or no marker.
    assert internal["stageImplementations"]["result"] == "real"
    page = (tmp_path / "out" / "review.html").read_text()
    assert "Stand-in steps: objects, reads. Their output is not from this scan." in page


def test_no_impl_dir_leaves_every_other_stage_unknown(tmp_path: Path):
    composed = _compose(tmp_path / "out", None)
    internal = composed.internal
    assert internal["stageImplementations"] == {**{stage: "unknown" for stage in _non_result_stages()}, "result": "real"}
    assert internal["standIns"] == []
    # The reviewer page stays silent when nothing ran as a stand-in.
    page = (tmp_path / "out" / "review.html").read_text()
    assert "Stand-in steps" not in page


def test_a_marker_without_the_schema_is_refused_by_name(tmp_path: Path):
    impl_dir = tmp_path / "impl"
    impl_dir.mkdir()
    (impl_dir / "poses.json").write_text(json.dumps({"schema": "hss-stage-impl/v2", "stage": "poses"}))
    try:
        _compose(tmp_path / "out", impl_dir)
    except ResultError as exc:
        assert exc.code == "impl_invalid"
        assert "poses.json" in str(exc)
    else:
        raise AssertionError("a marker without the pinned schema must stop the composition")


def test_result_json_is_byte_identical_with_and_without_impl_files(tmp_path: Path):
    impl_dir = _write_impl_dir(tmp_path, {stage: "real" for stage in _non_result_stages()})
    _compose(tmp_path / "with", impl_dir)
    _compose(tmp_path / "without", None)
    with_bytes = (tmp_path / "with" / "result.json").read_bytes()
    without_bytes = (tmp_path / "without" / "result.json").read_bytes()
    assert with_bytes == without_bytes
    # The app-facing result carries no impl fields; only the internal record does.
    assert "stageImplementations" not in json.loads(with_bytes)
