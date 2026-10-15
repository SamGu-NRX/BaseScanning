"""Exercise the real reads worker and its storage consumer without provider calls."""

from __future__ import annotations

import hashlib
from io import BytesIO

import pytest
from PIL import Image

from server.contracts.jobs import CommittedFile
from server.orchestrator.early.reads import on_commit
from server.orchestrator.stateio import get_json
from server.orchestrator.tests.conftest import build_services
from server.reads.aggregate import build_reads
from server.reads.cache import still_key
from server.reads.config import ReadsConfig
from server.reads.types import StillResult


def _services_and_file(tmp_path, monkeypatch, purpose, raw):
    services, _, _ = build_services(tmp_path, "memory")
    services.settings.VISION_EXTERNAL_CALLS = "on"
    cfg = ReadsConfig(external_vision=False)
    monkeypatch.setattr(ReadsConfig, "from_env", classmethod(lambda cls, settings=None: cfg))
    item = CommittedFile(
        path="stills/s00001.jpg",
        role="still",
        sha256=hashlib.sha256(raw).hexdigest(),
        bytes=len(raw),
        generation="1",
        key="uploads/cap_test/stills/s00001.jpg",
        meta={"id": "s00001", "purpose": purpose},
    )
    services.storage.put_bytes(item.key, raw, "image/jpeg")
    return services, item, cfg


@pytest.mark.parametrize("purpose", ["meter_close", "panel_close"])
def test_early_read_artifact_survives_dispatch_replay_and_aggregation(tmp_path, monkeypatch, purpose):
    image = BytesIO()
    Image.new("RGB", (16, 16), "white").save(image, format="JPEG")
    services, item, cfg = _services_and_file(tmp_path, monkeypatch, purpose, image.getvalue())

    on_commit(services, "cap_test", [item])

    state_key = f"early:cap_test:reads:{item.path}"
    summary = get_json(services.state, state_key)
    assert "error" not in summary
    artifact_key = still_key("cap_test", "s00001")
    raw = services.storage.get_bytes(artifact_key)
    result = StillResult.model_validate_json(raw)
    assert result.still.still_id == "s00001"
    assert result.still.sha256 == item.sha256
    assert result.answers and all(answer.error == "disabled" for answer in result.answers)
    assert summary["cost_usd"] == result.cost_usd
    if purpose == "meter_close":
        assert result.meter is not None
    else:
        assert result.panel is not None

    def unexpected_image_read(*args, **kwargs):
        raise AssertionError("a repeated commit must use the worker's stored StillResult")

    monkeypatch.setattr("server.reads.worker.prepare", unexpected_image_read)
    on_commit(services, "cap_test", [item])
    assert "error" not in get_json(services.state, state_key)
    assert services.storage.get_bytes(artifact_key) == raw

    reads = build_reads("cap_test", services.storage, cfg)
    assert reads.status == "disabled"
    assert "s00001" in reads.timings_s
    assert reads.cost_usd == 0.0


def test_failed_early_read_does_not_publish_an_invalid_result(tmp_path, monkeypatch):
    services, item, cfg = _services_and_file(tmp_path, monkeypatch, "meter_close", b"not an image")

    on_commit(services, "cap_test", [item])

    summary = get_json(services.state, f"early:cap_test:reads:{item.path}")
    assert "error" in summary
    assert not services.storage.exists(still_key("cap_test", "s00001"))
    assert build_reads("cap_test", services.storage, cfg).status == "partial"
