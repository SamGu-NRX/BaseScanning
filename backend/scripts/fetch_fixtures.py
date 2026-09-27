#!/usr/bin/env python3
"""Fetch golden fixtures listed in data/fixtures/manifest.json.

`FIXTURE_SOURCE` is a local directory (default: the store's `internal/` folder)
or a `gs://` URL. The documented mirror is `gs://hss-fixtures` with the same
relative paths; that bucket is not published yet. Payloads are written only
under the cache (default `.fixtures/`). Committed items are verified in place.

Extracted packet files are checked against the manifest too. A stale extract is
re-extracted from the cached tar, or the tar is re-fetched when that still
disagrees.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from server.common.config import default_fixture_source  # noqa: E402
from server.contracts.packet_resolve import PacketLayoutError, resolve_packet_manifest  # noqa: E402

MANIFEST = REPO / "data" / "fixtures" / "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_file(item: dict, cache: Path) -> Path | None:
    rel = item.get("cache_path")
    if not rel:
        return None
    text = str(rel)
    if text.startswith(".fixtures/"):
        text = text[len(".fixtures/") :]
    return cache / text


def _select(items: list[dict], only: str | None, item_id: str | None) -> list[dict]:
    if item_id:
        chosen = [item for item in items if item["id"] == item_id]
        if not chosen:
            print(f"missing: {item_id}", file=sys.stderr)
            raise SystemExit(2)
        return chosen
    if only == "all":
        return items
    if only == "large":
        return [item for item in items if not item["committed"]]
    return [item for item in items if item["committed"]]


def _local_parts(root: Path, relative: str) -> list[Path] | None:
    direct = root / relative
    if direct.is_file():
        return [direct]
    parts = sorted(direct.parent.glob(direct.name + ".part-*"))
    if parts:
        return parts
    return None


def _run(command: list[str]) -> bool:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr)
        return False
    return True


def _download_gs(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tools = []
    if shutil.which("gcloud"):
        tools.append(["gcloud", "storage", "cp", url, str(dest)])
    if shutil.which("gsutil"):
        tools.append(["gsutil", "cp", url, str(dest)])
    if not tools:
        print("gs:// fetch needs gcloud or gsutil; gs://hss-fixtures is not published yet", file=sys.stderr)
        return False
    for command in tools:
        if dest.exists():
            dest.unlink()
        if _run(command) and dest.is_file():
            return True
    return False


def _materialise(source: str, relative: str, dest: Path) -> bool:
    """Copy `relative` from the source into `dest`. Split `*.part-*` are concatenated."""

    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")
    if source.startswith("gs://"):
        base = source.rstrip("/") + "/" + relative
        if _download_gs(base, partial):
            partial.replace(dest)
            return True
        parts: list[Path] = []
        index = 0
        while True:
            part_dest = dest.parent / f"{dest.name}.part-{index:02d}"
            if not _download_gs(f"{base}.part-{index:02d}", part_dest):
                if part_dest.exists():
                    part_dest.unlink()
                break
            parts.append(part_dest)
            index += 1
        if not parts:
            return False
        with dest.open("wb") as out:
            for part in parts:
                out.write(part.read_bytes())
        return True
    parts = _local_parts(Path(source), relative)
    if not parts:
        return False
    with dest.open("wb") as out:
        for part in parts:
            out.write(part.read_bytes())
    return True


def _reject(path: Path, message: str) -> None:
    if path.is_file() and ".fixtures" in path.parts:
        path.unlink()
    print(message, file=sys.stderr)
    raise SystemExit(3)


def _check_hash(path: Path, item: dict, *, delete_on_mismatch: bool) -> None:
    failure = _cache_failure(path, item)
    if failure is None:
        return
    if delete_on_mismatch and path.is_file():
        path.unlink()
    print(f"{failure}: {item['id']}", file=sys.stderr)
    raise SystemExit(3)


def _cache_failure(path: Path, item: dict) -> str | None:
    expected = item.get("sha256")
    if expected and _sha256(path) != expected:
        return "sha256 mismatch"
    expected_bytes = item.get("bytes")
    if expected_bytes is not None and path.stat().st_size != int(expected_bytes):
        return "size mismatch"
    return None


def _packet_house(item: dict) -> str | None:
    """House id for a committed packet row, or None when the row is not one."""

    if item.get("kind") != "packet":
        return None
    repo_path = item.get("repo_path")
    if not repo_path:
        return None
    parts = Path(str(repo_path)).parts
    if parts[:3] != ("data", "fixtures", "packets") or len(parts) < 5:
        return None
    return parts[3]


def _short_hash(value: str | None) -> str:
    if not value:
        return "absent"
    return value[:8] + "…"


def _rows_for_house(items: list[dict], house: str) -> list[dict]:
    return [item for item in items if _packet_house(item) == house]


def _listed_mismatches(house: str, packet_path: Path) -> list[str]:
    """Files named by an extracted packet manifest whose bytes disagree."""

    try:
        document = json.loads(packet_path.read_text())
    except (OSError, json.JSONDecodeError):
        return [f"{house}/{packet_path.name} is not a packet manifest"]
    files = document.get("files")
    if not isinstance(files, list):
        return []
    root = packet_path.parent
    messages: list[str] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        rel = entry.get("path")
        expected = entry.get("sha256")
        if not isinstance(rel, str) or not isinstance(expected, str) or not expected:
            continue
        relative = Path(rel)
        if relative.is_absolute() or ".." in relative.parts:
            messages.append(f"{house}/{rel} is outside the packet")
            continue
        path = root / relative
        actual = _sha256(path) if path.is_file() else None
        if actual != expected:
            messages.append(f"{house}/{rel} is {_short_hash(actual)}, manifest {_short_hash(expected)}")
    return messages


def _extracted_mismatches(cache: Path, house: str, rows: list[dict]) -> list[str]:
    """Extracted packet manifests versus the fixture manifest, then their files."""

    messages: list[str] = []
    packet_paths: list[Path] = []
    for item in rows:
        name = Path(str(item["repo_path"])).name
        path = cache / "packets" / house / name
        packet_paths.append(path)
        expected = item.get("sha256")
        if not isinstance(expected, str) or not expected:
            continue
        actual = _sha256(path) if path.is_file() else None
        expected_bytes = item.get("bytes")
        size_bad = path.is_file() and expected_bytes is not None and path.stat().st_size != int(expected_bytes)
        label = f"{house}/{name}"
        if actual != expected:
            messages.append(f"{label} is {_short_hash(actual)}, manifest {_short_hash(expected)}")
        elif size_bad:
            messages.append(f"{label} is {path.stat().st_size} bytes, manifest {int(expected_bytes)}")
    if messages:
        return messages
    for path in packet_paths:
        if path.is_file():
            messages.extend(_listed_mismatches(house, path))
    return messages


def _orphan_listed(cache: Path, house: str) -> list[str]:
    house_dir = cache / "packets" / house
    messages: list[str] = []
    for path in sorted(house_dir.glob("packet*.json")):
        messages.extend(_listed_mismatches(house, path))
    return messages


def _format_mismatch(messages: list[str]) -> str:
    shown = messages[:5]
    text = "; ".join(shown)
    extra = len(messages) - len(shown)
    if extra:
        text += f"; and {extra} more"
    return text


def _replace_extracted(tar_path: Path, cache: Path, house: str) -> None:
    house_dir = cache / "packets" / house
    if house_dir.exists():
        shutil.rmtree(house_dir)
    _extract_packet(tar_path, cache)


def _align_extracted(cache: Path, source: str, tar_item: dict, items: list[dict]) -> None:
    """Re-extract, or re-fetch then re-extract, when extracted files disagree."""

    house = str(tar_item["id"])
    dest = _cache_file(tar_item, cache)
    if dest is None:
        return
    rows = _rows_for_house(items, house)
    house_dir = cache / "packets" / house
    present = house_dir.is_dir() and any(house_dir.glob("packet*.json"))
    if not present:
        if not dest.is_file():
            return
        _replace_extracted(dest, cache, house)
    bad = _extracted_mismatches(cache, house, rows) if rows else _orphan_listed(cache, house)
    if not bad:
        return
    if dest.is_file() and _cache_failure(dest, tar_item) is None:
        print(f"extracted sha256 mismatch: {_format_mismatch(bad)}; re-extracting {house}", file=sys.stderr)
        _replace_extracted(dest, cache, house)
        bad = _extracted_mismatches(cache, house, rows) if rows else _orphan_listed(cache, house)
        if not bad:
            print(f"re-extracted {house}")
            return
    print(f"extracted sha256 mismatch: {_format_mismatch(bad)}; re-fetching {house}", file=sys.stderr)
    if dest.is_file():
        dest.unlink()
    relative = tar_item.get("source")
    if not relative or not _materialise(source, str(relative), dest):
        print(f"missing: {house}", file=sys.stderr)
        raise SystemExit(2)
    _check_hash(dest, tar_item, delete_on_mismatch=True)
    _replace_extracted(dest, cache, house)
    bad = _extracted_mismatches(cache, house, rows) if rows else _orphan_listed(cache, house)
    if bad:
        print(f"extracted sha256 mismatch: {_format_mismatch(bad)}", file=sys.stderr)
        raise SystemExit(3)
    print(f"re-fetched {house}")


def refresh_present_extractions(cache: Path, source: str, items: list[dict], *, done: set[str]) -> None:
    """Check extracts already on disk, including when this run did not select their tars."""

    for item in items:
        if item.get("kind") != "packet_tar" or _mirror_skip(item):
            continue
        house = str(item["id"])
        if house in done:
            continue
        house_dir = cache / "packets" / house
        if not (house_dir.is_dir() and any(house_dir.glob("packet*.json"))):
            continue
        _align_extracted(cache, source, item, items)
        done.add(house)


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members: list[tarfile.TarInfo] = []
    for member in tar.getmembers():
        parts = Path(member.name).parts
        if member.name.startswith("/") or ".." in parts:
            raise SystemExit(3)
        members.append(member)
    return members


def _extract_packet(tar_path: Path, cache: Path) -> None:
    dest = cache / "packets"
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path) as tar:
        tar.extractall(dest, members=_safe_members(tar), filter="data")
    for folder in sorted(path for path in dest.iterdir() if path.is_dir()):
        try:
            resolve_packet_manifest(folder)
        except PacketLayoutError as exc:
            if "holds both" in str(exc):
                print(str(exc), file=sys.stderr)
                raise SystemExit(3) from exc


def _link_ply(repo: Path, cache_file: Path) -> None:
    """Point the committed scene's relative asset path at the cached PLY.

    The link is gitignored. The PLY bytes stay under the cache.
    """

    try:
        relative = cache_file.resolve().relative_to((repo / ".fixtures").resolve())
    except ValueError:
        return
    parts = relative.parts
    scene_dir: Path | None = None
    if len(parts) >= 3 and parts[0] == "gt":
        scene_dir = repo / "data" / "fixtures" / "scenes" / parts[1] / parts[2]
    elif parts and parts[0] == "scenes":
        scene_dir = repo / "data" / "fixtures" / Path(*parts[:-1])
    if scene_dir is None or not (scene_dir / "scene.json").is_file():
        return
    link = scene_dir / cache_file.name
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        return
    link.symlink_to(os.path.relpath(cache_file, link.parent))


def _verify_committed(repo: Path, item: dict) -> None:
    repo_path = item.get("repo_path")
    if not repo_path:
        print(f"missing: {item['id']}", file=sys.stderr)
        raise SystemExit(2)
    path = repo / repo_path
    if path.is_dir():
        try:
            path = resolve_packet_manifest(path)
        except PacketLayoutError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(3) from exc
    if not path.is_file():
        print(f"missing: {item['id']}", file=sys.stderr)
        raise SystemExit(2)
    _check_hash(path, item, delete_on_mismatch=False)
    print(f"ok   {item['id']}")


def _mirror_skip(item: dict) -> str | None:
    """LaMAR URLs and unmirrored tars are registry rows, not store copies.

    `scripts/lamar/fetch_lamar.sh` fetches the URLs. `unmirrored:` marks a
    local tar the store does not have. Either way `make fixtures` must not
    fail the rest of the manifest.
    """

    source = str(item.get("source") or "")
    if source.startswith("unmirrored:"):
        return "unmirrored"
    if source.startswith(("http://", "https://")):
        return "external"
    return None


def _fetch_one(repo: Path, source: str, cache: Path, item: dict) -> None:
    if item["committed"]:
        _verify_committed(repo, item)
        return
    skip = _mirror_skip(item)
    if skip:
        dest = _cache_file(item, cache)
        if dest is not None and dest.exists():
            if dest.is_file():
                _check_hash(dest, item, delete_on_mismatch=True)
            print(f"{skip} cached {item['id']}")
        else:
            print(f"{skip} {item['id']}")
        return
    relative = item.get("source")
    dest = _cache_file(item, cache)
    if not relative or dest is None:
        print(f"missing: {item['id']}", file=sys.stderr)
        raise SystemExit(2)
    failure = _cache_failure(dest, item) if dest.is_file() else None
    if failure is None and dest.is_file():
        print(f"cached {item['id']}")
    else:
        if failure is not None:
            print(f"{failure}: {item['id']}; re-fetching", file=sys.stderr)
            dest.unlink()
        if not _materialise(source, relative, dest):
            print(f"missing: {item['id']}", file=sys.stderr)
            raise SystemExit(2)
        _check_hash(dest, item, delete_on_mismatch=True)
        print(f"re-fetched {item['id']}" if failure else f"fetched {item['id']}")
    if dest.suffix == ".ply":
        _link_ply(repo, dest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=None, help="local store internal/ path, or gs://hss-fixtures")
    parser.add_argument("--only", choices=("small", "large", "all"), default="small")
    parser.add_argument("--item", default=None, help="one manifest id; overrides --only")
    parser.add_argument("--cache", default=None)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args(argv)
    source = args.source or os.environ.get("FIXTURE_SOURCE") or default_fixture_source()
    cache = Path(args.cache or os.environ.get("FIXTURE_CACHE") or ".fixtures")
    if not cache.is_absolute():
        cache = REPO / cache
    document = json.loads(args.manifest.read_text())
    if document.get("format") != "hss-fixtures/v0":
        print(f"unexpected manifest format {document.get('format')!r}", file=sys.stderr)
        return 3
    all_items = document["items"]
    items = _select(all_items, None if args.item else args.only, args.item)
    missing: list[str] = []
    aligned: set[str] = set()
    for item in items:
        try:
            _fetch_one(REPO, source, cache, item)
        except SystemExit as exc:
            if exc.code == 2:
                missing.append(item["id"])
                continue
            raise
        if item.get("kind") == "packet_tar" and _mirror_skip(item) is None:
            _align_extracted(cache, source, item, all_items)
            aligned.add(str(item["id"]))
    refresh_present_extractions(cache, source, all_items, done=aligned)
    if missing:
        print("missing: " + " ".join(missing), file=sys.stderr)
        return 2
    print(f"fixtures ok ({len(items)} items) source={source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
