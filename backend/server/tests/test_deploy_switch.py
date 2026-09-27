from __future__ import annotations

import json
import os

import pytest

from server.api.finalize import render_finalize
from server.common.backends.state_modal_dict import ModalDictState
from server.common.backends.storage_local import LocalFsStorage
from server.common.deploy_switch import baked_stage_stubs, finalize_eta_s, modal_app_name, optional_runtime_env
from server.contracts.jobs import CaptureRecord
from server.orchestrator.api import start_run
from server.orchestrator.artifacts import hydrate_packet
from server.orchestrator.modal_functions import use_shared_dev_backends
from server.orchestrator.services import bind, current, packet_sha256


def test_dev_honours_stubs_off_only_with_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HSS_ORCH_MODAL_REMOTE", "1")
    monkeypatch.setenv("HSS_ORCH_STAGE_STUBS", "0")
    assert baked_stage_stubs("dev") == "0"
    monkeypatch.delenv("HSS_ORCH_MODAL_REMOTE")
    assert baked_stage_stubs("dev") == "1"
    monkeypatch.setenv("HSS_ORCH_MODAL_REMOTE", "1")
    monkeypatch.delenv("HSS_ORCH_STAGE_STUBS")
    assert baked_stage_stubs("dev") == "1"
    assert baked_stage_stubs("prod") is None


def test_optional_runtime_env_is_empty_unless_the_shell_sets_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HSS_ORCH_PREWARM", raising=False)
    monkeypatch.delenv("HSS_ORCH_TEST_KILL_COORDINATOR_AFTER", raising=False)
    monkeypatch.delenv("MODAL_APP_NAME", raising=False)
    assert optional_runtime_env() == {}
    assert modal_app_name() == "house-scanning"
    monkeypatch.setenv("HSS_ORCH_PREWARM", "0")
    monkeypatch.setenv("HSS_ORCH_TEST_KILL_COORDINATOR_AFTER", "validate")
    monkeypatch.setenv("MODAL_APP_NAME", "hss-probe")
    assert optional_runtime_env() == {
        "HSS_ORCH_PREWARM": "0",
        "HSS_ORCH_TEST_KILL_COORDINATOR_AFTER": "validate",
    }
    assert modal_app_name() == "hss-probe"


def test_finalize_eta_tracks_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HSS_ORCH_MODAL_REMOTE", raising=False)
    assert finalize_eta_s("processing") == 180
    assert finalize_eta_s("awaiting_files") == 200
    assert render_finalize("processing", [], "run_x")["etaS"] == 180
    monkeypatch.setenv("HSS_ORCH_MODAL_REMOTE", "1")
    assert render_finalize("processing", [], "run_x")["etaS"] == 1500
    assert render_finalize("awaiting_files", ["packet.json"], "run_x")["etaS"] == 200


def test_api_run_hydrates_in_the_worker_config(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run written with the capture-api env is readable by a worker env."""

    monkeypatch.setenv("SMP_ENVIRONMENT", "dev")
    monkeypatch.setenv("GIT_SHA", "abc123")
    monkeypatch.setenv("HSS_ORCH_MODAL_REMOTE", "1")
    monkeypatch.setenv("HSS_ORCH_STAGE_STUBS", "1")
    monkeypatch.setenv("VISION_EXTERNAL_CALLS", "off")
    monkeypatch.delenv("MODAL_TASK_ID", raising=False)
    import importlib

    import server.modal_app as modal_app

    modal_app = importlib.reload(modal_app)
    api_env = modal_app.deploy_env("capture-api")
    worker_env = modal_app.deploy_env("pipeline")
    for key in ("STATE_BACKEND", "STORAGE_BACKEND", "STATE_DICT_NAME"):
        assert api_env[key] == worker_env[key]
    assert api_env["STATE_BACKEND"] == "modal_dict"
    assert api_env["STORAGE_BACKEND"] == "gcs"
    for key, value in api_env.items():
        monkeypatch.setenv(key, value)

    mapping: dict = {}
    root = tmp_path / "bucket"
    root.mkdir()

    def resolve_state(settings):
        assert settings.STATE_BACKEND == "modal_dict"
        assert settings.STATE_DICT_NAME == api_env["STATE_DICT_NAME"]
        return ModalDictState(mapping)

    def resolve_storage(settings):
        assert settings.STORAGE_BACKEND == "gcs"
        return LocalFsStorage(root)

    monkeypatch.setattr("server.orchestrator.services.resolve_state", resolve_state)
    monkeypatch.setattr("server.orchestrator.services.resolve_storage", resolve_storage)
    monkeypatch.setattr("server.orchestrator.api.RunCoordinator.drive", lambda self, run_id: None)

    packet = {
        "keyframes": [{"id": "k0"}],
        "stills": [],
        "taps": [{"id": "meter", "element": "meter"}],
        "scaleReference": {"ext": {"doorOnMeterSide": False, "doorViews": []}},
    }
    raw = json.dumps(packet).encode()
    api = current()
    api.state.put_capture(
        CaptureRecord(
            capture_id="cap_handoff",
            packet_id="packet-handoff",
            status="awaiting_files",
            tier="arkit",
            flow="guided",
            device_model="iPhone",
            owner_principal="test",
            created_at=api.clock.iso(),
            finalize_by=api.clock.iso(),
        )
    )
    uri = "captures/cap_handoff/packet.json"
    api.storage.put_bytes(uri, raw, "application/json")
    with bind(api):
        run = start_run("cap_handoff", packet_sha256(raw), uri)

    monkeypatch.setenv("MODAL_TASK_ID", "ta-handoff")
    use_shared_dev_backends()
    assert os.environ["STORAGE_BACKEND"] == "gcs"
    assert os.environ["STATE_BACKEND"] == "modal_dict"
    assert os.environ["STATE_DICT_NAME"] == api_env["STATE_DICT_NAME"]
    worker = current()
    assert worker is not api
    assert worker.state.get_run(run.run_id) is not None
    packet_dir = tmp_path / "hydrated"
    packet_dir.mkdir()
    hydrate_packet(worker.storage, worker.state, "cap_handoff", packet_dir)
    assert (packet_dir / "packet.json").read_bytes() == raw
