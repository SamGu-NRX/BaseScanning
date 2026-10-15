"""Remote hydration fetches declared inputs and reuses a packet cache."""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from server.contracts.jobs import FileRecord, RunRecord, StageRecord
from server.orchestrator.artifacts import (
    hydrate_artifacts,
    hydrate_packet,
    packet_cache_dir,
    purge_packet_cache,
)
from server.orchestrator.graph import STAGE_NAMES, select_stage_artifacts, stage_needs_packet_files
from server.orchestrator.services import bind
from server.orchestrator.tests.conftest import build_services


def test_every_stage_declares_inputs() -> None:
    assert stage_needs_packet_files("poses") is True
    assert stage_needs_packet_files("scale") is True
    assert stage_needs_packet_files("coverage") is False
    assert stage_needs_packet_files("result") is False
    for name in STAGE_NAMES:
        assert select_stage_artifacts(name, {}) == {}


def test_scale_keeps_pose_files_and_drops_the_model(tmp_path: Path) -> None:
    artifacts = {
        "poses/cameras.json": "poses/cameras.json",
        "poses/sparse.ply": "poses/sparse.ply",
        "poses/tracks.npz": "poses/tracks.npz",
        "poses/registration_report.json": "poses/registration_report.json",
        "poses/database.db": "poses/database.db",
        "poses/sparse/0/cameras.txt": "poses/sparse/0/cameras.txt",
        "reads/reads.json": "reads/reads.json",
        "objects/objects.json": "objects/objects.json",
        "dense/dense.ply": "dense/dense.ply",
    }
    selected = select_stage_artifacts("scale", artifacts)
    assert "poses/database.db" not in selected
    assert "poses/sparse/0/cameras.txt" not in selected
    assert "dense/dense.ply" not in selected
    assert set(selected) == {
        "poses/cameras.json",
        "poses/sparse.ply",
        "poses/tracks.npz",
        "poses/registration_report.json",
        "reads/reads.json",
        "objects/objects.json",
    }
    services, _uri, _digest = build_services(tmp_path, "memory")
    for key in selected:
        services.storage.put_bytes(key, key.encode(), "application/json")
    services.storage.put_bytes("poses/database.db", b"db", "application/octet-stream")
    run = RunRecord(
        run_id="run_scale",
        capture_id="cap_test",
        packet_sha256="a" * 64,
        status="running",
        branch="image",
        flow="guided",
        tier="arkit",
        profile_default="C",
        created_at=services.clock.iso(),
        deadline_at=services.clock.iso(),
        artifacts={**artifacts, "poses/database.db": "poses/database.db"},
    )
    rec = StageRecord(run_id=run.run_id, stage="scale", status="running", attempt=1, heartbeat=services.clock.iso())
    with bind(services):
        ctx = services.stage_context(run, rec, "call")
    assert (ctx.work_dir / "poses" / "cameras.json").read_bytes() == b"poses/cameras.json"
    assert not (ctx.work_dir / "poses" / "database.db").exists()
    assert not (ctx.work_dir / "dense" / "dense.ply").exists()


def test_coverage_skips_packet_images(tmp_path: Path) -> None:
    services, uri, digest = build_services(tmp_path, "memory")
    services.storage.put_bytes("captures/cap_test/keyframes/k0.jpg", b"jpeg", "image/jpeg")
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
            key="captures/cap_test/keyframes/k0.jpg",
        )
    )
    run = RunRecord(
        run_id="run_cov",
        capture_id="cap_test",
        packet_sha256=digest,
        status="running",
        branch="image",
        flow="guided",
        tier="arkit",
        profile_default="C",
        created_at=services.clock.iso(),
        deadline_at=services.clock.iso(),
        artifacts={"packet_uri": uri},
    )
    rec = StageRecord(run_id=run.run_id, stage="coverage", status="running", attempt=1, heartbeat=services.clock.iso())
    with bind(services):
        ctx = services.stage_context(run, rec, "call")
    packet_dir = Path(ctx.inputs["packet_dir"])
    assert (packet_dir / "packet.json").is_file()
    assert not (packet_dir / "keyframes" / "k0.jpg").exists()


def test_packet_cache_skips_the_second_download(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HSS_PACKET_CACHE", str(tmp_path / "cache"))
    services, _uri, _digest = build_services(tmp_path, "memory")
    services.storage.put_bytes("captures/cap_test/keyframes/k0.jpg", b"jpeg", "image/jpeg")
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
            key="captures/cap_test/keyframes/k0.jpg",
        )
    )
    cache = packet_cache_dir("cap_test")
    assert cache is not None
    first = tmp_path / "first"
    hydrate_packet(services.storage, services.state, "cap_test", first, include_files=True, cache_dir=cache)
    assert (first / "keyframes" / "k0.jpg").read_bytes() == b"jpeg"
    assert (cache / ".complete").is_file()

    class _Closed:
        def get_bytes(self, key: str) -> bytes:
            raise AssertionError(key)

        def exists(self, key: str) -> bool:
            raise AssertionError(key)

    second = tmp_path / "second"
    hydrate_packet(_Closed(), services.state, "cap_test", second, include_files=True, cache_dir=cache)
    assert (second / "keyframes" / "k0.jpg").read_bytes() == b"jpeg"
    manifest_only = tmp_path / "json-only"
    hydrate_packet(_Closed(), services.state, "cap_test", manifest_only, include_files=False, cache_dir=cache)
    assert (manifest_only / "packet.json").is_file()
    assert not (manifest_only / "keyframes").exists()


def test_packet_cache_is_keyed_by_capture_and_sha(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HSS_PACKET_CACHE", str(tmp_path / "cache"))
    base = packet_cache_dir("cap_test", "a" * 64)
    amended = packet_cache_dir("cap_test", "b" * 64)
    assert base is not None and amended is not None
    assert base != amended
    base.mkdir(parents=True)
    (base / "packet.json").write_text('{"packet":"one"}')
    (base / ".complete").write_text("ok")
    amended.mkdir(parents=True)
    assert not (amended / "packet.json").exists()


def test_purge_packet_cache_removes_the_whole_capture_slot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HSS_PACKET_CACHE", str(tmp_path / "cache"))
    base = packet_cache_dir("cap_test", "a" * 64)
    assert base is not None
    (base / "photos").mkdir(parents=True)
    (base / "packet.json").write_bytes(b"{}")
    (base / ".complete").write_bytes(b"ok")
    (base / "photos" / "k0.jpg").write_bytes(b"jpeg")
    other = packet_cache_dir("cap_other")
    assert other is not None
    other.mkdir(parents=True)
    (other / "keep.txt").write_bytes(b"keep")

    removed, failed = purge_packet_cache("cap_test")

    assert (removed, failed) == (3, [])
    assert not (tmp_path / "cache" / "cap_test").exists()
    assert (other / "keep.txt").is_file()


def test_purge_packet_cache_reports_paths_it_could_not_remove(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HSS_PACKET_CACHE", str(tmp_path / "cache"))
    base = packet_cache_dir("cap_test")
    assert base is not None
    base.mkdir(parents=True)
    (base / "packet.json").write_bytes(b"{}")

    def broken_rmtree(path, *args, **kwargs):
        raise OSError("volume busy")

    monkeypatch.setattr(shutil, "rmtree", broken_rmtree)
    removed, failed = purge_packet_cache("cap_test")
    assert removed == 0
    assert failed == [str(base / "packet.json")]


def test_hydrate_skips_a_tombstoned_capture(tmp_path: Path) -> None:
    """A worker mid-run must not restore the cache DELETE just purged."""

    cache = tmp_path / "cache" / "cap_test"
    cache.mkdir(parents=True)
    (cache / "packet.json").write_bytes(b"{}")
    (cache / ".complete").write_bytes(b"ok")

    class _TombstonedState:
        def __init__(self) -> None:
            self.asked: list[str] = []

        def get_kv(self, key: str):
            self.asked.append(key)
            return {"deleted_at": "2026-10-15T00:00:00Z"}

    state = _TombstonedState()
    dest = tmp_path / "packet"
    hydrate_packet(object(), state, "cap_test", dest, include_files=True, cache_dir=cache)
    assert not dest.exists()
    assert state.asked == ["tomb:cap_test"]


def test_artifact_downloads_overlap(tmp_path: Path) -> None:
    class _Slow:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.inflight = 0
            self.peak = 0

        def get_bytes(self, key: str) -> bytes:
            with self._lock:
                self.inflight += 1
                self.peak = max(self.peak, self.inflight)
            time.sleep(0.05)
            with self._lock:
                self.inflight -= 1
            return key.encode()

        def exists(self, key: str) -> bool:
            raise AssertionError("point download does not stat first")

        def list_prefix(self, prefix: str) -> list[str]:
            return []

    storage = _Slow()
    artifacts = {f"reads/part-{index}.json": f"reads/part-{index}.json" for index in range(8)}
    written = hydrate_artifacts(storage, tmp_path / "out", artifacts)
    assert len(written) == 8
    assert storage.peak > 1
