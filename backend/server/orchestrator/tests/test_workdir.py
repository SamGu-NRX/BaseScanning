"""Packet files, prior artifacts, and publish must not blank storage."""

from __future__ import annotations

import json
from pathlib import Path

from server.contracts.jobs import FileRecord, StageRecord, StageResult
from server.orchestrator.api import start_run
from server.orchestrator.artifacts import hydrate_artifacts, is_contract_artifact
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
    attempt = services.work_root / run.run_id / "result" / "attempt-1"
    (work,) = list(attempt.iterdir())
    packet_dir = work / "packet"
    assert (packet_dir / "packet.json").read_bytes() == raw
    # Result reads scene and verdicts, not the capture frames or the pose model.
    assert not (packet_dir / "features" / "k0.bin").exists()
    assert not (packet_dir / "keyframes" / "k0.jpg").exists()
    assert not (work / "poses" / "cameras.json").exists()
    assert json.loads((work / "scene" / "scene.json").read_text())["format"] == "base-scene/v0"
    scale_packet = services.work_root / run.run_id / "scale" / "attempt-1" / "packet"
    assert (scale_packet / "keyframes" / "k0.jpg").read_bytes() == b"jpeg"
    assert (scale_packet / "features" / "k0.bin").read_bytes() == b"feat"
    assert (services.work_root / run.run_id / "scale" / "attempt-1" / "poses" / "cameras.json").is_file()


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
    # Dense reads poses, not the parent scene. Declared parent files stay under parent/.
    poses = Path(ctx.inputs["parent/poses/cameras.json"])
    assert poses.is_file()
    assert poses.parent.parent.name == "parent"
    assert not (ctx.work_dir / "poses" / "cameras.json").exists()
    assert "parent/scene/scene.json" not in ctx.inputs
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


# server.poses.run (WS-04) returns every file under poses/, keyed by the work-dir relative path.
# server.objects.run.run_3d (WS-07) returns these two §3.9 keys.
_WS04_POSE_FILES = (
    "poses/cameras.json",
    "poses/sparse.ply",
    "poses/tracks.npz",
    "poses/registration_report.json",
    "poses/sparse/0/cameras.txt",
    "poses/sparse/0/frames.txt",
    "poses/sparse/0/images.txt",
    "poses/sparse/0/points3D.txt",
    "poses/sparse/0/rigs.txt",
    "poses/fragments/fixed/cameras.txt",
    "poses/fragments/prior/cameras.txt",
)
_WS07_OBJECT_FILES = ("objects/objects_3d.json", "objects/view_requests.json")
_I18_FILES = (
    "dense/cloud.npz",
    "dense/tracks.npz",
    "dense/cameras_w.json",
    "coverage/scene_ready.json",
)
_OVERSIZED = "poses/sparse/0/huge.bin"


def test_publisher_uploads_ws04_and_ws07_returned_keys(tmp_path: Path) -> None:
    objects_source = (Path(__file__).resolve().parents[3] / "server" / "objects" / "run.py").read_text()
    assert 'objects_3d_key = "objects/objects_3d.json"' in objects_source
    assert 'view_requests_key = "objects/view_requests.json"' in objects_source
    for key in (
        *_WS04_POSE_FILES,
        *_WS07_OBJECT_FILES,
        *_I18_FILES,
        "poses/sparse/0/",
        "poses/fragments/",
        "dense/depth/",
    ):
        assert is_contract_artifact(key), key
    assert not is_contract_artifact("scale/report.json")

    services, uri, digest = build_services(tmp_path, "memory")
    with bind(services):
        run = start_run("cap_test", digest, uri)
    rec = services.state.get_stage(run.run_id, "poses")
    assert rec is not None
    ctx = services.stage_context(run, rec, "call")
    ctx.work_dir = tmp_path / "stage-out"
    ctx.work_dir.mkdir()
    returned_files = (*_WS04_POSE_FILES, *_WS07_OBJECT_FILES, *_I18_FILES, "scale/report.json")
    for key in returned_files:
        dest = ctx.work_dir / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(key.encode())
    oversized = ctx.work_dir / _OVERSIZED
    oversized.write_bytes(b"x" * (5_000_000 + 1))
    pose_artifacts = {
        str(path.relative_to(ctx.work_dir)): str(path.relative_to(ctx.work_dir))
        for path in (ctx.work_dir / "poses").rglob("*")
        if path.is_file()
    }
    assert set(_WS04_POSE_FILES) <= set(pose_artifacts)
    assert _OVERSIZED in pose_artifacts
    assert oversized.stat().st_size > 5_000_000
    returned = {
        **pose_artifacts,
        **{key: key for key in (*_WS07_OBJECT_FILES, *_I18_FILES, "scale/report.json")},
    }
    stored = _publish(ctx, StageResult(artifacts=returned, metrics={}, warnings=[]))
    assert set(stored) == set(returned)
    expected = {key: (ctx.work_dir / key).read_bytes() for key in returned}
    for key, storage_key in stored.items():
        assert services.storage.get_bytes(storage_key) == expected[key]
    restored = tmp_path / "next-work"
    written = hydrate_artifacts(services.storage, restored, stored)
    assert set(written) == set(returned)
    for key in returned:
        assert (restored / key).read_bytes() == expected[key]
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"nope")
    rejected = _publish(
        ctx,
        StageResult(
            artifacts={
                "../secret": "poses/cameras.json",
                "poses/from-outside.bin": str(outside),
                "reads/escaped.bin": "../outside.bin",
            },
            metrics={},
            warnings=[],
        ),
    )
    assert rejected == {}
    prefix = f"captures/cap_test/runs/{run.run_id}"
    assert not services.storage.exists(f"{prefix}/poses/from-outside.bin")
    assert not services.storage.exists(f"{prefix}/reads/escaped.bin")
    assert services.storage.get_bytes(stored["poses/cameras.json"]) == expected["poses/cameras.json"]
    skipped = hydrate_artifacts(services.storage, restored, {"../secret": stored["poses/cameras.json"]})
    assert skipped == {}
