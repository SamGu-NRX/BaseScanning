"""HSS_ORCH_REAL_STAGES: per-stage real-vs-stand-in selection and the impl markers.

Unit tests parse the variable and its refusals. Journey tests drive whole runs
in-process with the committed mini-arkit packet fixture: with stubs on and
``validate`` selected, the run must reach today's terminal state through the
real packet validation, and every stage must publish its impl marker under the
pinned hss-stage-impl/v1 contract. No Modal, GPU, network or credentials.
"""

from __future__ import annotations

import functools
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
from server.orchestrator.config import OrchConfig, load_orch
from server.orchestrator.executor import InlineExecutor
from server.orchestrator.graph import STAGE_NAMES
from server.orchestrator.runner import stage_callable
from server.orchestrator.services import Services, bind, packet_sha256
from server.orchestrator.stubs import result as stub_result

FIXTURE = Path(__file__).resolve().parents[3] / "data" / "fixtures" / "packets" / "mini-arkit"
PACKET_RAW = (FIXTURE / "packet.json").read_bytes()
PACKET = json.loads(PACKET_RAW)

IMPL_SCHEMA = "hss-stage-impl/v1"
VISION_VAR = "VISION_EXTERNAL_CALLS"


def _env(**overrides: str) -> dict[str, str]:
    env = {stage_selection.ENV_VAR: "validate", stage_selection.ENVIRONMENT_VAR: "dev"}
    env.update(overrides)
    return env


@pytest.fixture
def dev_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests that set HSS_ORCH_REAL_STAGES in os.environ need an explicit dev process."""

    monkeypatch.setenv(stage_selection.ENVIRONMENT_VAR, "dev")


# --- parsing -----------------------------------------------------------------


def test_unset_selects_nothing() -> None:
    assert stage_selection.parse_real_stages({}) == frozenset()
    assert stage_selection.parse_real_stages({stage_selection.ENV_VAR: "   "}) == frozenset()


def test_single_stage_and_whitespace() -> None:
    assert stage_selection.parse_real_stages(_env()) == frozenset({"validate"})
    assert stage_selection.parse_real_stages(_env(**{stage_selection.ENV_VAR: " validate , scale ,"})) == frozenset(
        {"validate", "scale"}
    )


def test_cpu_alias_covers_every_weightless_stage() -> None:
    selected = stage_selection.parse_real_stages(_env(**{stage_selection.ENV_VAR: "cpu"}))
    assert selected == frozenset(stage_selection.CPU_STAGES)
    # objects loads OWLv2/SigLIP; dense falls back to π³/MoGe/DA3 on photo-only packets.
    assert "objects" not in selected
    assert "dense" not in selected
    assert selected | {"objects", "dense"} == frozenset(STAGE_NAMES)


def test_dense_can_still_be_listed_explicitly() -> None:
    assert stage_selection.parse_real_stages(_env(**{stage_selection.ENV_VAR: "cpu,dense"})) == frozenset(
        {*stage_selection.CPU_STAGES, "dense"}
    )


@pytest.mark.parametrize("value", ["on", "ON", " on ", "1", "true", "yes"])
def test_cpu_alias_refused_when_vision_calls_are_on(value: str) -> None:
    """reads and criteria call a paid vision model when VISION_EXTERNAL_CALLS is on."""

    with pytest.raises(ValueError, match=VISION_VAR) as excinfo:
        stage_selection.parse_real_stages(_env(**{stage_selection.ENV_VAR: "cpu", VISION_VAR: value}))
    assert repr(value) in str(excinfo.value)


@pytest.mark.parametrize("value", [None, "off", "0", ""])
def test_cpu_alias_allowed_when_vision_calls_are_off(value: str | None) -> None:
    env = _env(**{stage_selection.ENV_VAR: "cpu"})
    if value is not None:
        env[VISION_VAR] = value
    assert stage_selection.parse_real_stages(env) == frozenset(stage_selection.CPU_STAGES)


def test_explicit_reads_with_vision_on_is_the_callers_choice() -> None:
    selected = stage_selection.parse_real_stages(_env(**{stage_selection.ENV_VAR: "reads", VISION_VAR: "on"}))
    assert selected == frozenset({"reads"})


def test_unknown_stage_names_the_variable_and_value() -> None:
    with pytest.raises(ValueError, match=stage_selection.ENV_VAR) as excinfo:
        stage_selection.parse_real_stages(_env(**{stage_selection.ENV_VAR: "poses,wat"}))
    assert "unknown stage 'wat'" in str(excinfo.value)


def test_remote_refusal_names_both_variables() -> None:
    with pytest.raises(ValueError, match="HSS_ORCH_MODAL_REMOTE") as excinfo:
        stage_selection.parse_real_stages(_env(**{stage_selection.REMOTE_VAR: "1"}))
    assert stage_selection.ENV_VAR in str(excinfo.value)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_remote_refusal_accepts_every_spelling_load_orch_treats_as_true(value: str) -> None:
    assert load_orch({stage_selection.REMOTE_VAR: value}).modal_remote is True
    with pytest.raises(ValueError, match=stage_selection.REMOTE_VAR) as excinfo:
        stage_selection.parse_real_stages(_env(**{stage_selection.REMOTE_VAR: value}))
    assert repr(value) in str(excinfo.value)


@pytest.mark.parametrize("value", ["0", "false", "off", ""])
def test_remote_false_spellings_do_not_refuse(value: str) -> None:
    assert load_orch({stage_selection.REMOTE_VAR: value}).modal_remote is False
    assert stage_selection.parse_real_stages(_env(**{stage_selection.REMOTE_VAR: value})) == frozenset({"validate"})


def test_non_dev_environment_refusal_names_the_value() -> None:
    with pytest.raises(ValueError, match="production") as excinfo:
        stage_selection.parse_real_stages(_env(**{stage_selection.ENVIRONMENT_VAR: "production"}))
    assert stage_selection.ENVIRONMENT_VAR in str(excinfo.value)


@pytest.mark.parametrize("value", [None, "", "   ", "Dev", " dev", "prod"])
def test_environment_settings_reads_as_prod_is_refused(value: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed: Settings coerces a missing, blank or unknown SMP_ENVIRONMENT to
    prod, so the selection must treat exactly those values as prod too."""

    if value is None:
        monkeypatch.delenv(stage_selection.ENVIRONMENT_VAR, raising=False)
    else:
        monkeypatch.setenv(stage_selection.ENVIRONMENT_VAR, value)
    assert Settings().SMP_ENVIRONMENT == "prod"

    env = {stage_selection.ENV_VAR: "validate"}
    if value is not None:
        env[stage_selection.ENVIRONMENT_VAR] = value
    with pytest.raises(ValueError, match=stage_selection.ENVIRONMENT_VAR) as excinfo:
        stage_selection.parse_real_stages(env)
    assert "prod" in str(excinfo.value)


def test_runner_import_refuses_a_selection_without_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unconfigured process (no SMP_ENVIRONMENT) is prod and must not start with a selection."""

    import server.orchestrator.runner as runner_module

    monkeypatch.delenv(stage_selection.ENVIRONMENT_VAR, raising=False)
    monkeypatch.setenv(stage_selection.ENV_VAR, "validate")
    try:
        with pytest.raises(ValueError, match=stage_selection.ENVIRONMENT_VAR):
            importlib.reload(runner_module)
    finally:
        monkeypatch.delenv(stage_selection.ENV_VAR, raising=False)
        importlib.reload(runner_module)  # restore a fully initialised module


def test_objects_refused_without_local_weights_device() -> None:
    with pytest.raises(ValueError, match=stage_selection.OBJECTS_DEVICE_VAR) as excinfo:
        stage_selection.parse_real_stages(_env(**{stage_selection.ENV_VAR: "objects"}))
    assert "weights" in str(excinfo.value)
    with pytest.raises(ValueError, match=stage_selection.OBJECTS_DEVICE_VAR):
        stage_selection.parse_real_stages(_env(**{stage_selection.ENV_VAR: "cpu,objects"}))


def test_objects_allowed_with_cpu_device() -> None:
    selected = stage_selection.parse_real_stages(
        _env(**{stage_selection.ENV_VAR: "objects", stage_selection.OBJECTS_DEVICE_VAR: "cpu"})
    )
    assert selected == frozenset({"objects"})


@pytest.mark.usefixtures("dev_environment")
def test_selection_cache_follows_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(stage_selection.ENV_VAR, raising=False)
    assert stage_selection.is_real("validate") is False
    monkeypatch.setenv(stage_selection.ENV_VAR, "validate")
    assert stage_selection.is_real("validate") is True
    monkeypatch.setenv(stage_selection.ENV_VAR, "scale")
    assert stage_selection.is_real("validate") is False
    assert stage_selection.is_real("scale") is True


# --- callable selection -------------------------------------------------------


@pytest.mark.usefixtures("dev_environment")
def test_selected_stage_runs_real_under_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(stage_selection.ENV_VAR, "validate")
    validate = stage_callable("validate", stubs=True)
    assert validate.__module__ == "server.packet"
    # A stage outside the selection keeps today's behaviour exactly.
    poses = stage_callable("poses", stubs=True)
    assert poses.__module__ == "server.orchestrator.stubs.poses"
    # The stubs_exempt stage stays real, as today.
    placement = stage_callable("placement", stubs=True)
    assert placement.__module__ == "server.placement.adapter"


def test_unselected_stages_keep_the_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(stage_selection.ENV_VAR, raising=False)
    assert stage_callable("validate", stubs=True).__module__ == "server.orchestrator.stubs.validate"
    # Stubs off runs everything real, selection or not.
    assert stage_callable("poses", stubs=False).__module__ == "server.poses"


@pytest.mark.usefixtures("dev_environment")
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
        monkeypatch.setenv(stage_selection.ENVIRONMENT_VAR, "dev")
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
    # Record the markers the result stage actually receives in its work dir, so
    # the test covers delivery through hydration and not only publication.
    # functools.wraps keeps the stub's module and name, which the marker reads.
    stub_result_run = stub_result.run
    delivered: list[set[str]] = []

    @functools.wraps(stub_result_run)
    def result_run(ctx):
        delivered.append({path.stem for path in (ctx.work_dir / "impl").glob("*.json")})
        return stub_result_run(ctx)

    monkeypatch.setattr(stub_result, "run", result_run)
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

    # The result stage received every upstream marker, and not its own: impl/result.json
    # is published only after the result callable returns.
    assert delivered == [set(STAGE_NAMES) - {"result"}]


def test_unset_selection_records_today_implementations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    services, run, capture = _run_journey(tmp_path, monkeypatch, None)
    _assert_same_terminal_state(run, capture)

    # Today: every stage except stubs_exempt placement runs a stand-in.
    assert _impl_body(services, run, "validate")["implementation"] == "stand-in"
    assert _impl_body(services, run, "poses")["implementation"] == "stand-in"
    assert _impl_body(services, run, "placement")["implementation"] == "real"
    assert _impl_body(services, run, "placement")["callable"] == "server.placement.adapter.run"
    assert _impl_body(services, run, "result")["implementation"] == "stand-in"
