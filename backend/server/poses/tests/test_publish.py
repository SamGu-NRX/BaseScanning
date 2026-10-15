"""Stage publish returns every file under poses/, including ones over 5 MB."""

from __future__ import annotations

from pathlib import Path

from server.contracts.jobs import StageContext, StageResult
from server.poses import run

_OVER = 5_000_000 + 1


def test_run_publishes_pose_files_over_five_megabytes(tmp_path: Path, monkeypatch) -> None:
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    (packet_dir / "packet.json").write_text("{}")
    work = tmp_path / "work"

    def fake_load(_path, **_kwargs):
        return object()

    def fake_run_poses(_view, out_dir, **_kwargs):
        poses = Path(out_dir) / "poses"
        sparse = poses / "sparse" / "0"
        fragment = poses / "fragments" / "prior"
        sparse.mkdir(parents=True)
        fragment.mkdir(parents=True)
        blob = b"\0" * _OVER
        (poses / "cameras.json").write_text("[]")
        (poses / "registration_report.json").write_text("{}")
        (poses / "sparse.ply").write_bytes(blob)
        (poses / "tracks.npz").write_bytes(blob)
        (poses / "database.db").write_bytes(blob)
        (poses / "database.db-wal").write_bytes(b"wal")
        (poses / "database.db-shm").write_bytes(b"shm")
        (poses / "_database.db").write_bytes(blob)
        images = poses / "_images"
        images.mkdir()
        (images / "00.jpg").write_bytes(b"jpg")
        scratch = poses / "_tmp_map"
        scratch.mkdir()
        (scratch / "junk.txt").write_text("x")
        (sparse / "points3D.bin").write_bytes(blob)
        (fragment / "cameras.json").write_text("[]")
        return {
            "frames_registered": 1,
            "registered_fraction": 1.0,
            "timings_s": {"mapper_s": 0.0},
            "warnings": [],
            "actions": [],
        }

    monkeypatch.setattr("server.poses.load_packet", fake_load)
    monkeypatch.setattr("server.poses.run_poses", fake_run_poses)
    ctx = StageContext(
        capture_id="cap_test",
        run_id="run_test",
        stage="poses",
        attempt=1,
        branch="image",
        flow="photo",
        tier="photo_only",
        work_dir=work,
        inputs={"packet_dir": str(packet_dir)},
        deadline_at="2026-09-27T00:00:00Z",
        heartbeat=lambda _substep: None,
        vlm_budget_usd=0.0,
        storage=None,
        state=None,
        settings=None,
    )
    result = run(ctx)
    assert isinstance(result, StageResult)
    expected = {
        "poses/cameras.json",
        "poses/registration_report.json",
        "poses/sparse.ply",
        "poses/tracks.npz",
        "poses/database.db",
        "poses/sparse/0/points3D.bin",
        "poses/fragments/prior/cameras.json",
    }
    assert set(result.artifacts) == expected
    assert "poses/_images/00.jpg" not in result.artifacts
    assert "poses/database.db-wal" not in result.artifacts
    assert "poses/database.db-shm" not in result.artifacts
    assert "poses/_database.db" not in result.artifacts
    assert "poses/_tmp_map/junk.txt" not in result.artifacts
    assert all(result.artifacts[key] == key for key in expected)
    assert (work / "poses" / "database.db").stat().st_size == _OVER
    assert (work / "poses" / "sparse" / "0" / "points3D.bin").stat().st_size == _OVER
