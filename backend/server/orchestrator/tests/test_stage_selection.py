"""HSS_ORCH_REAL_STAGES: per-stage real-vs-stand-in selection and the impl markers.

Unit tests parse the variable and its refusals. Journey tests drive whole runs
in-process with the committed mini-arkit packet fixture: with stubs on and
``validate`` selected, the run must reach today's terminal state through the
real packet validation, and every stage must publish its impl marker under the
pinned hss-stage-impl/v1 contract. No Modal, GPU, network or credentials.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from server.common.backends.state_memory import MemoryState
from server.common.backends.storage_local import LocalFsStorage
from server.common.config import Settings
from server.contracts.jobs import CaptureRecord
from server.orchestrator import stage_selection
from server.orchestrator.api import start_run
from server.orchestrator.clock import Clock
from server.orchestrator.config import OrchConfig
from server.orchestrator.executor import InlineExecutor
from server.orchestrator.graph import STAGE_NAMES, select_stage_artifacts
from server.orchestrator.runner import stage_callable
from server.orchestrator.services import Services, bind, packet_sha256

FIXTURE = Path(__file__).resolve().parents[4] / "data" / "fixtures" / "packets" / "mini-arkit"
PACKET_RAW = (FIXTURE / "packet.json").read_bytes()
PACKET = json.loads(PACKET_RAW)

IMPL_SCHEMA = "hss-stage-impl/v1"


def _env(**overrides: str) -> dict[str, str]:
    env = {stage_selection.ENV_VAR: "validate"}
    env.update(overrides)
    return env


# --- parsing -----------------------------------------------------------------


def test_unset_selects_nothing() -> None:
    assert stage_selection.parse_real_stages({}) == frozenset()
    assert stage_selection.parse_real_stages({stage_selection.ENV_VAR: "   "}) == frozenset()


def test_single_stage_and_whitespace() -> None:
    assert stage_selection.parse_real_stages(_env()) == frozenset({"validate"})
    assert stage_selection.parse_real_stages({stage_selection.ENV_VAR: " validate , scale ,"}) == frozenset(
        {"validate", "scale"}
    )


def test_cpu_alias_covers_every_weightless_stage() -> None:
    selected = stage_selection.parse_real_stages({stage_selection.ENV_VAR: "cpu"})
    assert selected == frozenset(stage_selection.CPU_STAGES)
    assert "objects" not in selected
    assert selected | {"objects"} == frozenset(STAGE_NAMES)


def test_unknown_stage_names_the_variable_and_value() -> None:
    with pytest.raises(ValueError, match=stage_selection.ENV_VAR) as excinfo:
        stage_selection.parse_real_stages({stage_selection.ENV_VAR: "poses,wat"})
    assert "wat" in str(excinfo.value)


def test_remote_refusal_names_both_variables() -> None:
    with pytest.raises(ValueError, match="HSS_ORCH_MODAL_REMOTE") as excinfo:
        stage_selection.parse_real_stages(_env(**{stage_selection.REMOTE_VAR: "1"}))
    assert stage_selection.ENV_VAR in str(excinfo.value)


def test_non_dev_environment_refusal_names_the_value() -> None:
    with pytest.raises(ValueError, match="production") as excinfo:
        stage_selection.parse_real_stages(_env(**{stage_selection.ENVIRONMENT_VAR: "production"}))
    assert stage_selection.ENVIRONMENT_VAR in str(excinfo.value)


def test_objects_refused_without_local_weights_device() -> None:
    with pytest.raises(ValueError, match=stage_selection.OBJECTS_DEVICE_VAR) as excinfo:
        stage_selection.parse_real_stages({stage_selection.ENV_VAR: "objects"})
    assert "weights" in str(excinfo.value)
    with pytest.raises(ValueError, match=stage_selection.OBJECTS_DEVICE_VAR):
        stage_selection.parse_real_stages({stage_selection.ENV_VAR: "cpu,objects"})


def test_objects_allowed_with_cpu_device() -> None:
    selected = stage_selection.parse_real_stages(
        {stage_selection.ENV_VAR: "objects", stage_selection.OBJECTS_DEVICE_VAR: "cpu"}
    )
    assert selected == frozenset({"objects"})


def test_selection_cache_follows_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(stage_selection.ENV_VAR, raising=False)
    assert stage_selection.is_real("validate") is False
    monkeypatch.setenv(stage_selection.ENV_VAR, "validate")
    assert stage_selection.is_real("validate") is True
    monkeypatch.setenv(stage_selection.ENV_VAR, "scale")
    assert stage_selection.is_real("validate") is False
    assert stage_selection.is_real("scale") is True


# --- callable selection -------------------------------------------------------


def test_selected_stage_runs_real_under_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(stage_selection.ENV_VAR, "validate")
    validate = stage_callable("validate", stubs=True)
    assert validate.__module__ == "server.packet"
    # A stage outside the selection keeps today's behaviour exactly.
    poses = stage_callable("poses", stubs=True)
    assert poses.__module__ == "server.orchestrator.stubs.poses"
    # The stubs_exempt stage stays real, as today.
    placement = stage_callable("placement", stubs=True)
    assert placement.__module__ == "server.placement"


def test_unselected_stages_keep_the_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(stage_selection.ENV_VAR, raising=False)
    assert stage_callable("validate", stubs=True).__module__ == "server.orchestrator.stubs.validate"
    # Stubs off runs everything real, selection or not.
    assert stage_callable("poses", stubs=False).__module__ == "server.poses"


def test_runner_import_rejects_a_bad_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusals land at startup: importing the runner is the first thing a
    stage-running process does, so a bad value raises there, not on stage one."""

    import server.orchestrator.runner as runner_module

    monkeypatch.setenv(stage_selection.ENV_VAR, "not-a-stage")
    with pytest.raises(ValueError, match="not-a-stage"):
        importlib.reload(runner_module)
    monkeypatch.delenv(stage_selection.ENV_VAR, raising=False)
    importlib.reload(runner_module)  # restore a fully initialised module


# --- journey: a whole run under the selection ---------------------------------


def _mini_arkit_services(tmp_path: Path) -> tuple[Services, str, str]:
    """The conftest fixture's minimal packet fails real validation, so the
    journey uses the committed mini-arkit packet with its files in storage."""

    settings = Settings(
        STATE_BACKEND="memory",
        STORAGE_BACKEND="local",
        VISION_EXTERNAL_CALLS="off",
        HARD_STOP_MIN=20,
        VOLUME_ROOT=str(tmp_path),
        SMP_ENVIRONMENT="dev",
    )
    config = OrchConfig(stage_stubs=True, stale_s=180, max_respawns=2, prewarm=True)
    storage = LocalFsStorage(tmp_path / "store")
    services = Services(
        settings=settings,
        orch=config,
        state=MemoryState(),
        storage=storage,
        clock=Clock(),
        executor=InlineExecutor(config=config, stubs=True),
        work_root=tmp_path / "work",
    )
    uri = "captures/cap_mini/packet.json"
    services.storage.put_bytes(uri, PACKET_RAW, "application/json")
    for entry in PACKET["files"]:
        services.storage.put_bytes(
            f"captures/cap_mini/{entry['path']}", (FIXTURE / entry["path"]).read_bytes(), "application/octet-stream"
        )
    services.state.put_capture(
        CaptureRecord(
            capture_id="cap_mini",
            packet_id=PACKET["packetId"],
            status="awaiting_files",
            tier="arkit",
            flow="guided",
            device_model="iPhone",
            owner_principal="test",
            created_at=services.clock.iso(),
            finalize_by=services.clock.iso(),
        )
    )
    return services, uri, packet_sha256(PACKET_RAW)


def _run_journey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str | None):
    if selection is None:
        monkeypatch.delenv(stage_selection.ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(stage_selection.ENV_VAR, selection)
    services, uri, digest = _mini_arkit_services(tmp_path)
    with bind(services):
        run = start_run("cap_mini", digest, uri)
    capture = services.state.get_capture("cap_mini")
    return services, run, capture


def _impl_body(services: Services, run, stage: str) -> dict:
    return json.loads(services.storage.get_bytes(run.artifacts[f"impl/{stage}.json"]))


def _assert_same_terminal_state(run, capture) -> None:
    assert run.status == "done"
    assert capture.status in {"complete", "needs_views", "manual_review"}


def test_real_validate_journey_marks_mixed_implementations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    services, run, capture = _run_journey(tmp_path, monkeypatch, "validate")
    _assert_same_terminal_state(run, capture)

    # Every stage that ran left one marker under the pinned contract.
    for stage in STAGE_NAMES:
        body = _impl_body(services, run, stage)
        assert body["schema"] == IMPL_SCHEMA
        assert body["stage"] == stage
    assert _impl_body(services, run, "validate") == {
        "schema": IMPL_SCHEMA,
        "stage": "validate",
        "implementation": "real",
        "callable": "server.packet.run",
    }
    poses_body = _impl_body(services, run, "poses")
    assert poses_body["implementation"] == "stand-in"
    assert poses_body["callable"] == "server.orchestrator.stubs.poses.run"
    # placement is stubs_exempt: real even on the stub path, as today.
    assert _impl_body(services, run, "placement")["implementation"] == "real"
    assert _impl_body(services, run, "result")["implementation"] == "stand-in"

    # The result stage's hydration carries the whole impl/ directory.
    selected = select_stage_artifacts("result", run.artifacts)
    for stage in STAGE_NAMES:
        assert f"impl/{stage}.json" in selected


def test_unset_selection_records_today_implementations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    services, run, capture = _run_journey(tmp_path, monkeypatch, None)
    _assert_same_terminal_state(run, capture)

    # Today: every stage except stubs_exempt placement runs a stand-in.
    assert _impl_body(services, run, "validate")["implementation"] == "stand-in"
    assert _impl_body(services, run, "poses")["implementation"] == "stand-in"
    assert _impl_body(services, run, "placement")["implementation"] == "real"
    assert _impl_body(services, run, "placement")["callable"] == "server.placement.run"
    assert _impl_body(services, run, "result")["implementation"] == "stand-in"
