"""In-process services bound for one test."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from server.common.backends.state_memory import MemoryState
from server.common.backends.storage_local import LocalFsStorage

try:
    from server.common.backends import state_modal_dict as modal_state
except ImportError:
    modal_state = None  # WS-01 owns this module; it is absent until that branch is merged.
from server.common.config import Settings
from server.contracts.jobs import CaptureRecord
from server.orchestrator.clock import Clock
from server.orchestrator.config import OrchConfig
from server.orchestrator.executor import InlineExecutor
from server.orchestrator.services import Services, bind, packet_sha256


def open_state(backend: str):
    """``memory`` is in-process. ``modal_dict`` is WS-01's module, including its prefix helper."""
    if backend == "memory":
        return MemoryState()
    if backend != "modal_dict":
        raise AssertionError(backend)
    if modal_state is None:
        pytest.skip("WS-01 owns server.common.backends.state_modal_dict")
    state_cls = modal_state.ModalDictState
    live = os.environ.get("HSS_LIVE_DICT")
    if not live:
        return state_cls()
    environment_name = os.environ.get("MODAL_ENVIRONMENT", "dev")
    try:
        shared = state_cls.from_name(live, environment_name=environment_name)
    except TypeError:
        shared = state_cls.from_name(live)
    prefix_cls = getattr(modal_state, "PrefixKv", None)
    kv = getattr(shared, "kv", None)
    if prefix_cls is None or kv is None:
        return shared
    return state_cls(prefix_cls(kv, uuid.uuid4().hex + "/"))


def build_services(tmp_path: Path, backend: str, **orch: object) -> tuple[Services, str, str]:
    settings = Settings(
        STATE_BACKEND="memory",
        STORAGE_BACKEND="local",
        VISION_EXTERNAL_CALLS="off",
        HARD_STOP_MIN=int(orch.pop("hard_stop_min", 20)),
        VOLUME_ROOT=str(tmp_path),
        SMP_ENVIRONMENT="dev",
    )
    stubs = bool(orch.pop("stage_stubs", True))
    config = OrchConfig(stage_stubs=stubs, stale_s=180, max_respawns=2, prewarm=True, **orch)  # type: ignore[arg-type]
    state = open_state(backend)
    storage = LocalFsStorage(tmp_path / "store")
    services = Services(
        settings=settings,
        orch=config,
        state=state,
        storage=storage,
        clock=Clock(),
        executor=InlineExecutor(config=config, stubs=stubs),
        work_root=tmp_path / "work",
    )
    packet = {
        "keyframes": [{"id": "k0"}],
        "stills": [],
        "taps": [{"id": "meter", "element": "meter"}],
        "scaleReference": {"ext": {"doorOnMeterSide": False, "doorViews": []}},
    }
    raw = json.dumps(packet).encode()
    uri = "captures/cap_test/packet.json"
    storage.put_bytes(uri, raw, "application/json")
    services.state.put_capture(
        CaptureRecord(
            capture_id="cap_test",
            packet_id="packet-test",
            status="awaiting_files",
            tier="arkit",
            flow="guided",
            device_model="iPhone",
            owner_principal="test",
            created_at=services.clock.iso(),
            finalize_by=services.clock.iso(),
        )
    )
    return services, uri, packet_sha256(raw)


@pytest.fixture(params=["memory", "modal_dict"])
def services(tmp_path: Path, request: pytest.FixtureRequest):
    built, uri, digest = build_services(tmp_path, request.param)
    with bind(built):
        yield built, uri, digest
