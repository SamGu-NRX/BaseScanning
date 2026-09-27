"""SD-92 registration counts and the checkpointed database name."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from server.poses.params import PoseParams
from server.poses.run import _checkpoint_database, _split_registration
from server.poses.sfm import MAPPING_THREADS, pipeline_options, registration_options


def test_mapping_and_registration_are_single_threaded() -> None:
    params = PoseParams(num_threads=4, seed=0)
    mapped = pipeline_options(params, refine=True, use_prior=True)
    registered = registration_options(params)
    assert params.num_threads == 4
    assert MAPPING_THREADS == 1
    assert mapped.num_threads == 1
    assert mapped.random_seed == 0
    assert registered.num_threads == 1
    assert registered.random_seed == 0


def test_split_counts_carried_frames_separately() -> None:
    rows = [
        {"name": "a.jpg", "pose_source": "sfm"},
        {"name": "b.jpg", "pose_source": "sfm"},
        {"name": "c.jpg", "pose_source": "sfm"},
        {"name": "d.jpg", "pose_source": "arkit"},
    ]
    counts = _split_registration(rows, ["a.jpg", "b.jpg"], "arkit_prior")
    assert counts == {
        "mapper": 2,
        "post_registered": 1,
        "carried": 1,
        "frames_registered": 4,
    }


def test_split_fixed_model_counts_every_frame_once() -> None:
    rows = [{"name": "a.jpg", "pose_source": "arkit"}, {"name": "b.jpg", "pose_source": "arkit"}]
    counts = _split_registration(rows, ["a.jpg"], "arkit_fixed")
    assert counts["mapper"] == 0
    assert counts["post_registered"] == 0
    assert counts["carried"] == 2
    assert counts["frames_registered"] == 2


def test_checkpoint_database_drops_wal_sidecars(tmp_path: Path) -> None:
    db_path = tmp_path / "_database.db"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE images (image_id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("INSERT INTO images (name) VALUES ('00.jpg')")
    connection.commit()
    connection.close()
    # COLMAP can leave these beside the database after the last connection closes.
    Path(f"{db_path}-wal").write_bytes(b"wal")
    Path(f"{db_path}-shm").write_bytes(b"shm")

    dest = _checkpoint_database(db_path)

    assert dest == tmp_path / "database.db"
    assert dest.is_file()
    assert not db_path.exists()
    assert not Path(f"{db_path}-wal").exists()
    assert not Path(f"{db_path}-shm").exists()
    assert not Path(f"{dest}-wal").exists()
    assert not Path(f"{dest}-shm").exists()
    check = sqlite3.connect(dest)
    try:
        assert check.execute("SELECT name FROM images").fetchall() == [("00.jpg",)]
    finally:
        check.close()
