"""fetch_fixtures exit codes, split parts, and --item overriding --only."""

import hashlib
import io
import json
import subprocess
import sys
import tarfile
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


def _members(payloads: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, payload in payloads.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _house_manifest(
    tmp_path: Path,
    tar_bytes: bytes,
    packet_bytes: bytes,
    *,
    extra: list[dict] | None = None,
    packet_committed: bool = True,
) -> Path:
    source = tmp_path / "src"
    archive = source / "data" / "packets" / "archives"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "house.tar").write_bytes(tar_bytes)
    manifest = tmp_path / "manifest.json"
    items = [
        {
            "id": "house",
            "kind": "packet_tar",
            "repo_path": None,
            "cache_path": ".fixtures/packets/house.tar",
            "source": "data/packets/archives/house.tar",
            "bytes": len(tar_bytes),
            "sha256": hashlib.sha256(tar_bytes).hexdigest(),
            "committed": False,
            "licence": "test",
        },
        {
            "id": "house-packet",
            "kind": "packet",
            "repo_path": "data/fixtures/packets/house/packet.arkit-lidar.json",
            "cache_path": None,
            "source": None,
            "bytes": len(packet_bytes),
            "sha256": hashlib.sha256(packet_bytes).hexdigest(),
            "committed": packet_committed,
            "licence": "test",
        },
    ]
    if extra:
        items.extend(extra)
    manifest.write_text(json.dumps({"format": "hss-fixtures/v0", "items": items}))
    return manifest


def _packet_bytes(note: bytes) -> bytes:
    document = {
        "files": [
            {
                "path": "note.txt",
                "bytes": len(note),
                "sha256": hashlib.sha256(note).hexdigest(),
            }
        ]
    }
    return json.dumps(document).encode()


def test_stale_extracted_packet_is_reextracted(tmp_path: Path) -> None:
    note = b"real"
    packet_bytes = _packet_bytes(note)
    tar_bytes = _members(
        {
            "house/packet.arkit-lidar.json": packet_bytes,
            "house/note.txt": note,
        }
    )
    manifest = _house_manifest(tmp_path, tar_bytes, packet_bytes)
    cache = tmp_path / "cache"
    (cache / "packets").mkdir(parents=True)
    (cache / "packets" / "house.tar").write_bytes(tar_bytes)
    stale = b'{"stale": true}\n'
    house = cache / "packets" / "house"
    house.mkdir()
    (house / "packet.arkit-lidar.json").write_bytes(stale)
    (house / "note.txt").write_bytes(b"old")
    result = _run(manifest, tmp_path / "src", cache, "--only", "large")
    assert result.returncode == 0, result.stderr
    assert f"{hashlib.sha256(stale).hexdigest()[:8]}" in result.stderr
    assert f"{hashlib.sha256(packet_bytes).hexdigest()[:8]}" in result.stderr
    assert "re-extracting house" in result.stderr
    assert "re-extracted house" in result.stdout
    assert (house / "packet.arkit-lidar.json").read_bytes() == packet_bytes
    assert (house / "note.txt").read_bytes() == note
    again = _run(manifest, tmp_path / "src", cache, "--only", "large")
    assert again.returncode == 0, again.stderr
    assert "re-extract" not in again.stderr
    assert "cached house" in again.stdout


def test_stale_listed_file_is_reextracted(tmp_path: Path) -> None:
    note = b"real"
    packet_bytes = _packet_bytes(note)
    tar_bytes = _members(
        {
            "house/packet.arkit-lidar.json": packet_bytes,
            "house/note.txt": note,
        }
    )
    manifest = _house_manifest(tmp_path, tar_bytes, packet_bytes)
    cache = tmp_path / "cache"
    (cache / "packets").mkdir(parents=True)
    (cache / "packets" / "house.tar").write_bytes(tar_bytes)
    house = cache / "packets" / "house"
    house.mkdir()
    (house / "packet.arkit-lidar.json").write_bytes(packet_bytes)
    (house / "note.txt").write_bytes(b"old")
    result = _run(manifest, tmp_path / "src", cache, "--only", "large")
    assert result.returncode == 0, result.stderr
    assert "house/note.txt" in result.stderr
    assert "re-extracting house" in result.stderr
    assert (house / "note.txt").read_bytes() == note


def test_stale_extract_refetches_when_the_cached_tar_disagrees(tmp_path: Path) -> None:
    note = b"real"
    packet_bytes = _packet_bytes(note)
    tar_bytes = _members({"house/packet.arkit-lidar.json": packet_bytes, "house/note.txt": note})
    manifest = _house_manifest(tmp_path, tar_bytes, packet_bytes)
    cache = tmp_path / "cache"
    (cache / "packets").mkdir(parents=True)
    (cache / "packets" / "house.tar").write_bytes(b"not-the-tar")
    house = cache / "packets" / "house"
    house.mkdir()
    (house / "packet.arkit-lidar.json").write_bytes(b'{"stale": true}\n')
    result = _run(manifest, tmp_path / "src", cache, "--only", "large")
    assert result.returncode == 0, result.stderr
    assert "re-fetching" in result.stderr
    assert (house / "packet.arkit-lidar.json").read_bytes() == packet_bytes
    assert (house / "note.txt").read_bytes() == note


def test_small_run_still_reextracts_a_present_packet(tmp_path: Path) -> None:
    note = b"real"
    packet_bytes = _packet_bytes(note)
    tar_bytes = _members({"house/packet.arkit-lidar.json": packet_bytes, "house/note.txt": note})
    anchor = REPO / "pyproject.toml"
    extra = [
        {
            "id": "anchor",
            "kind": "file",
            "repo_path": "pyproject.toml",
            "cache_path": None,
            "source": None,
            "bytes": anchor.stat().st_size,
            "sha256": _sha(anchor),
            "committed": True,
            "licence": "test",
        }
    ]
    manifest = _house_manifest(tmp_path, tar_bytes, packet_bytes, extra=extra, packet_committed=False)
    cache = tmp_path / "cache"
    (cache / "packets").mkdir(parents=True)
    (cache / "packets" / "house.tar").write_bytes(tar_bytes)
    house = cache / "packets" / "house"
    house.mkdir()
    (house / "packet.arkit-lidar.json").write_bytes(b'{"stale": true}\n')
    result = _run(manifest, tmp_path / "src", cache, "--only", "small")
    assert result.returncode == 0, result.stderr
    assert "re-extracting house" in result.stderr
    assert (house / "packet.arkit-lidar.json").read_bytes() == packet_bytes
    assert "cached house" not in result.stdout
