"""Packet files, prior artifacts, and publish must not blank storage."""

from __future__ import annotations

import json
from pathlib import Path

from server.contracts.jobs import FileRecord, StageRecord, StageResult
from server.orchestrator.api import start_run
from server.orchestrator.artifacts import is_contract_artifact
from server.orchestrator.graph import STAGES
from server.orchestrator.runner import _publish
from server.orchestrator.services import bind, packet_sha256
from server.orchestrator.tests.conftest import build_services


def test_stage_artifact_names_come_from_the_contract_table() -> None:
    for spec in STAGES.values():
        for key in spec.artifacts:
            assert is_contract_artifact(key), key


def test_packet_dir_and_prior_artifacts_are_on_disk(tmp_path: Path) -> None:
    services, uri, _digest = build_services(tmp_path, "memory")
    packet = json.loads(services.storage.get_bytes(uri))
    packet["files"] = [
        {
            "path": "features/k0.bin",
            "role": "features",
            "bytes": 4,
            "sha256": "ab" * 32,
        }
    ]
    raw = json.dumps(packet).encode()
    services.storage.put_bytes(uri, raw, "application/json")
    services.storage.put_bytes("captures/cap_test/features/k0.bin", b"feat", "application/octet-stream")
    services.state.upsert_file(
        FileRecord(
            capture_id="cap_test",
            path="keyframes/k0.jpg",
            role="keyframe",
            bytes=4,
            sha256="cd" * 32,
            md5="0" * 32,
            content_type="image/jpeg",
            state="committed",
            generation="1",
            key="captures/cap_test/keyframes/k0.jpg",
        )
    )
    services.storage.put_bytes("captures/cap_test/keyframes/k0.jpg", b"jpeg", "image/jpeg")
    with bind(services):
        run = start_run("cap_test", packet_sha256(raw), uri)
    work = services.work_root / run.run_id / "result" / "attempt-1"
    packet_dir = work / "packet"
    assert (packet_dir / "packet.json").read_bytes() == raw
    assert (packet_dir / "features" / "k0.bin").read_bytes() == b"feat"
    assert (packet_dir / "keyframes" / "k0.jpg").read_bytes() == b"jpeg"
    assert (work / "poses" / "cameras.json").is_file()
    assert json.loads((work / "scene" / "scene.json").read_text())["format"] == "base-scene/v0"
    assert (work / "poses" / "sparse" / "0" / ".keep").is_file()


def test_parent_artifacts_stay_under_parent(tmp_path: Path) -> None:
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        parent = start_run("cap_test", digest, uri)
        child = parent.model_copy(update={"run_id": "run_child", "parent_run_id": parent.run_id, "artifacts": {}})
        services.state.put_run(child)
        rec = StageRecord(
            run_id=child.run_id,
            stage="dense",
            status="pending",
            attempt=1,
            heartbeat=services.clock.iso(),
        )
        ctx = services.stage_context(child, rec, "call-child")
    scene = Path(ctx.inputs["parent/scene/scene.json"])
    assert scene.is_file()
    assert scene.parent.parent.name == "parent"
    assert not (ctx.work_dir / "scene" / "scene.json").exists()
    assert "poses" not in ctx.inputs


def test_missing_work_file_does_not_blank_storage(tmp_path: Path) -> None:
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        run = start_run("cap_test", digest, uri)
    key = f"captures/cap_test/runs/{run.run_id}/reads/reads.json"
    body = b'{"format":"base-reads/v0","capture_id":"cap_test","facts":[]}'
    services.storage.put_bytes(key, body, "application/json")
    rec = services.state.get_stage(run.run_id, "reads")
    assert rec is not None
    ctx = services.stage_context(run, rec, "call")
    ctx.work_dir = tmp_path / "empty"
    ctx.work_dir.mkdir()
    stored = _publish(
        ctx,
        StageResult(artifacts={"reads/reads.json": key}, metrics={}, warnings=[]),
    )
    assert services.storage.get_bytes(key) == body
    assert stored["reads/reads.json"] == key


def test_workdir_relative_path_is_what_gets_uploaded(tmp_path: Path) -> None:
    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        run = start_run("cap_test", digest, uri)
    rec = services.state.get_stage(run.run_id, "reads")
    assert rec is not None
    ctx = services.stage_context(run, rec, "call")
    dest = ctx.work_dir / "reads" / "reads.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = b'{"format":"base-reads/v0","capture_id":"cap_test","facts":[{"id":"m"}]}'
    dest.write_bytes(payload)
    canonical = f"captures/cap_test/runs/{run.run_id}/reads/reads.json"
    services.storage.put_bytes(canonical, b"previous", "application/json")
    stored = _publish(
        ctx,
        StageResult(artifacts={"reads/reads.json": "reads/reads.json"}, metrics={}, warnings=[]),
    )
    assert stored["reads/reads.json"] == canonical
    assert services.storage.get_bytes(canonical) == payload
