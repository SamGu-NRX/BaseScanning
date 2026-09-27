"""fetch_fixtures exit codes, split parts, and --item overriding --only."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "fetch_fixtures.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(manifest: Path, source: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(manifest),
            "--source",
            str(source),
            "--cache",
            str(cache),
            *args,
        ],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )


def test_split_parts_join_and_item_overrides_only(tmp_path: Path) -> None:
    source = tmp_path / "src"
    archive = source / "data" / "packets" / "archives"
    archive.mkdir(parents=True)
    payload = b"golden-tar-bytes"
    (archive / "house.tar.part-00").write_bytes(payload[:6])
    (archive / "house.tar.part-01").write_bytes(payload[6:])
    digest = hashlib.sha256(payload).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "hss-fixtures/v0",
                "items": [
                    {
                        "id": "house",
                        "kind": "file",
                        "repo_path": None,
                        "cache_path": ".fixtures/packets/house.tar",
                        "source": "data/packets/archives/house.tar",
                        "bytes": len(payload),
                        "sha256": digest,
                        "committed": False,
                        "licence": "test",
                    }
                ],
            }
        )
    )
    cache = tmp_path / "cache"
    result = _run(manifest, source, cache, "--only", "small", "--item", "house")
    assert result.returncode == 0, result.stderr
    fetched = cache / "packets" / "house.tar"
    assert fetched.read_bytes() == payload
    assert _sha(fetched) == digest


def test_missing_source_exits_2(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "hss-fixtures/v0",
                "items": [
                    {
                        "id": "gone",
                        "kind": "ply",
                        "repo_path": None,
                        "cache_path": ".fixtures/gt/gone.ply",
                        "source": "eval-harness/results/gone.ply",
                        "bytes": None,
                        "sha256": "a" * 64,
                        "committed": False,
                        "licence": "test",
                    }
                ],
            }
        )
    )
    result = _run(manifest, tmp_path / "empty", tmp_path / "cache", "--only", "large")
    assert result.returncode == 2
    assert "gone" in result.stderr


def test_sha_mismatch_exits_3_and_deletes_cache(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    blob = source / "cloud.ply"
    blob.write_bytes(b"not-the-real-cloud")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "hss-fixtures/v0",
                "items": [
                    {
                        "id": "cloud",
                        "kind": "ply",
                        "repo_path": None,
                        "cache_path": ".fixtures/gt/cloud.ply",
                        "source": "cloud.ply",
                        "bytes": blob.stat().st_size,
                        "sha256": "b" * 64,
                        "committed": False,
                        "licence": "test",
                    }
                ],
            }
        )
    )
    cache = tmp_path / "cache"
    result = _run(manifest, source, cache, "--only", "large")
    assert result.returncode == 3
    assert "sha256 mismatch" in result.stderr
    assert not (cache / "gt" / "cloud.ply").exists()


def test_external_and_unmirrored_rows_do_not_fail_all(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "hss-fixtures/v0",
                "items": [
                    {
                        "id": "lamar:lin-colmap",
                        "kind": "lamar",
                        "repo_path": None,
                        "cache_path": ".fixtures/lamar/colmap",
                        "source": "https://example.invalid/LIN.zip",
                        "bytes": None,
                        "sha256": None,
                        "committed": False,
                        "licence": "test",
                    },
                    {
                        "id": "yt-local",
                        "kind": "packet_tar",
                        "repo_path": None,
                        "cache_path": ".fixtures/packets/yt-local.tar",
                        "source": "unmirrored: local tar only",
                        "bytes": None,
                        "sha256": None,
                        "committed": False,
                        "licence": "test",
                    },
                ],
            }
        )
    )
    result = _run(manifest, tmp_path / "src", tmp_path / "cache", "--only", "all")
    assert result.returncode == 0
    assert "external lamar:lin-colmap" in result.stdout
    assert "unmirrored yt-local" in result.stdout
