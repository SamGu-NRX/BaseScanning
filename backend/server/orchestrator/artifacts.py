"""Stage work dirs and published bytes.

Artifact keys are the WS-00 §3.9 table, plus every other path a stage returns.
A stage reads ``work_dir/<key>``. The capture packet and committed files live in ``packet_dir`` (SD-17).
"""

from __future__ import annotations

import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from server.contracts.jobs import RunRecord
from server.orchestrator.graph import STAGE_NAMES
from server.orchestrator.stateio import get_json

# Exact keys from the §3.9 table, plus the directory keys the graph publishes.
CONTRACT_ARTIFACTS = frozenset(
    {
        "packet/validation.json",
        "packet/normalized.json",
        "packet/capture_check.json",
        "poses/cameras.json",
        "poses/sparse.ply",
        "poses/tracks.npz",
        "poses/registration_report.json",
        "poses/sparse/0/",
        "poses/fragments/",
        "poses/database.db",
        "scale/scale.json",
        "objects/objects.json",
        "objects/objects_3d.json",
        "objects/view_requests.json",
        "reads/reads.json",
        "dense/dense.ply",
        "dense/flagged.ply",
        "dense/depth/",
        "dense/dense_report.json",
        "dense/cloud.npz",
        "dense/tracks.npz",
        "dense/cameras_w.json",
        "coverage/coverage.npz",
        "coverage/bands.json",
        "coverage/points.ply",
        "coverage/scene_draft.json",
        "coverage/scene_ready.json",
        "coverage/coverage_report.json",
        "scene/scene.json",
        "scene/build_report.json",
        "scene/coverage_topdown.png",
        "scene/preview.ply",
        "placement/placement.json",
        "placement/scene.json",
        "verdicts/verdict.A.json",
        "verdicts/verdict.B.json",
        "verdicts/verdict.C.json",
        "verdicts/verdict.S.json",
        "verdicts/criteria_report.json",
        "verdicts/placement.C.json",
        "result/result.json",
        "result/result.internal.json",
        "result/review.html",
        "result/preview.ply",
        "run/timings.json",
        "run/cost.json",
        "run/stages.json",
    }
)

_SCENE_COPIES = frozenset({"coverage.npz", "points.ply", "dense.ply", "sparse.ply", "scale.json"})

# §3.9 directory rows. A stage returns the files inside the directory, not the trailing-slash key.
_DIRECTORY_PREFIXES = tuple(key for key in CONTRACT_ARTIFACTS if key.endswith("/"))


def overlay_stage_artifacts(state: object, run: RunRecord) -> RunRecord:
    """Recover outputs lost when parallel workers overwrite the shared run map.

    Read at consumer boundaries, not every coordinator tick. Keep storage reads
    uncached: scene merge can replace the bytes at an existing artifact key.
    """

    artifacts = dict(run.artifacts)
    for name in STAGE_NAMES:
        extra = get_json(state, f"stage_artifacts:{run.run_id}:{name}")
        if isinstance(extra, dict):
            artifacts.update({str(key): str(value) for key, value in extra.items()})
    return run.model_copy(update={"artifacts": artifacts})


def is_contract_artifact(key: str) -> bool:
    """True when ``key`` is a §3.9 name, a file inside a §3.9 directory, or a patterned sidecar."""

    if key in CONTRACT_ARTIFACTS or any(key.startswith(prefix) for prefix in _DIRECTORY_PREFIXES):
        return True
    if key.startswith("verdicts/placement."):
        return True
    if key.startswith(("verdicts/c1.", "verdicts/c2.", "verdicts/c3.")):
        return True
    return key.startswith("scene/") and Path(key).name in _SCENE_COPIES


def local_artifact(work_dir: Path, rel: str) -> Path | None:
    """A file or directory under ``work_dir``. Paths that resolve outside it are ignored."""

    if not rel:
        return None
    root = work_dir.resolve()
    candidate = Path(rel)
    if not candidate.is_absolute():
        candidate = work_dir / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if resolved != root and root not in resolved.parents:
        return None
    return resolved if resolved.exists() else None


def existing_object(storage: object, *keys: str) -> str | None:
    """The first storage key that already holds non-empty bytes."""

    for key in keys:
        if not key or key.startswith("/"):
            continue
        if not storage.exists(key):  # type: ignore[attr-defined]
            continue
        if storage.get_bytes(key):  # type: ignore[attr-defined]
            return key
    return None


class ReadThrough:
    """Memoize successful reads of immutable capture inputs.

    Use this for the packet and committed files. Do not use it for published
    artifacts: a later writer can replace the same key, and the cached bytes
    would hide that write. A miss is not stored, so a key that appears after
    an earlier lookup is fetched on the next read.
    """

    def __init__(self, storage: object) -> None:
        self.storage = storage
        self._bytes: dict[str, bytes] = {}
        self._lists: dict[str, list[str]] = {}

    def exists(self, key: str) -> bool:
        if key in self._bytes:
            return True
        return self.storage.exists(key)  # type: ignore[attr-defined]

    def get_bytes(self, key: str) -> bytes:
        if key not in self._bytes:
            self._bytes[key] = self.storage.get_bytes(key)  # type: ignore[attr-defined]
        return self._bytes[key]

    def list_prefix(self, prefix: str) -> list[str]:
        cached = self._lists.get(prefix)
        if cached:
            return list(cached)
        names = [str(name) for name in self.storage.list_prefix(prefix)]  # type: ignore[attr-defined]
        if names:
            self._lists[prefix] = names
        return names


_DOWNLOAD_WORKERS = 16


def packet_cache_dir(capture_id: str, packet_sha256: str = "") -> Path | None:
    """Volume slot for one packet. An amendment uses a different sha and must not reuse the base."""

    slot = Path(capture_id) / packet_sha256 if packet_sha256 else Path(capture_id)
    override = os.environ.get("HSS_PACKET_CACHE")
    if override:
        return Path(override) / slot
    if not os.environ.get("MODAL_TASK_ID") or not Path("/vol").is_dir():
        return None
    return Path("/vol/packet-cache") / slot


def purge_packet_cache(capture_id: str) -> tuple[int, list[str]]:
    """Remove every cached packet copy for a capture from the shared volume.

    DELETE calls this because object-storage prefixes do not cover the packet
    cache workers publish under /vol/packet-cache. Returns the number of files
    removed and the paths that survived, so the caller can report a partial
    purge instead of claiming a clean deletion.
    """

    base = packet_cache_dir(capture_id)
    if base is None or not base.is_dir():
        return 0, []
    files = [path for path in base.rglob("*") if path.is_file() and not path.is_symlink()]
    try:
        shutil.rmtree(base)
        return len(files), []
    except OSError:
        survivors = sorted(base.rglob("*"))
        kept = sum(1 for path in survivors if path.is_file() and not path.is_symlink())
        return len(files) - kept, [str(path) for path in survivors]


def _capture_tombstoned(state: object, capture_id: str) -> bool:
    """True once DELETE has removed the capture. States without kv storage never
    tombstone, and a failing lookup must not block a live capture's stage."""

    get_kv = getattr(state, "get_kv", None)
    if not callable(get_kv):
        return False
    try:
        return bool(get_kv(f"tomb:{capture_id}"))
    except Exception:
        return False


def hydrate_packet(
    storage: object,
    state: object,
    capture_id: str,
    packet_dir: Path,
    *,
    include_files: bool = True,
    cache_dir: Path | None = None,
    packet_key: str | None = None,
) -> None:
    """Copy the packet into ``packet_dir``.

    ``include_files`` is false for stages that only read ``packet.json``. A warm
    volume cache skips GCS for the frames a later stage does need.
    ``packet_key`` is the manifest's storage key: an amendment run passes its
    own ``captures/{capture_id}/amendments/{amendment_id}/packet.json``. A
    capture the API has deleted must not hydrate at all: restoring it would
    re-publish the packet cache DELETE just purged.
    """

    if _capture_tombstoned(state, capture_id):
        return
    packet_dir.mkdir(parents=True, exist_ok=True)
    if _copy_packet_cache(cache_dir, packet_dir, include_files=include_files):
        return
    manifest_key = packet_key or f"captures/{capture_id}/packet.json"
    _download_all(storage, [(packet_dir / "packet.json", manifest_key)])
    if not include_files:
        return
    _download_all(storage, _packet_file_jobs(state, capture_id, packet_dir, manifest_key))
    # A delete that lands mid-hydrate must be the last word: the entry check
    # above ran before the tombstone existed, so re-check immediately before
    # publishing, or this hydrate restores the cache the delete just purged.
    if cache_dir is not None and (packet_dir / "packet.json").is_file() and not _capture_tombstoned(state, capture_id):
        _publish_packet_cache(packet_dir, cache_dir)


def hydrate_artifacts(storage: object, dest_root: Path, artifacts: dict[str, str]) -> dict[str, str]:
    """Download the paths a stage asked for, in parallel."""

    written: dict[str, str] = {}
    jobs: list[tuple[Path, str]] = []
    for key, storage_key in artifacts.items():
        if _safe_rel(key) is None or not storage_key:
            continue
        dest = dest_root / key
        if key.endswith("/") or storage_key.endswith("/.keep"):
            prefix = storage_key[: -len(".keep")] if storage_key.endswith(".keep") else storage_key
            if not prefix.endswith("/"):
                prefix = prefix + "/"
            dest.mkdir(parents=True, exist_ok=True)
            names = list(storage.list_prefix(prefix))  # type: ignore[attr-defined]
            if not names:
                names = [storage_key]
            for name in names:
                if not str(name).startswith(prefix):
                    continue
                target = dest / str(name)[len(prefix) :]
                jobs.append((target, str(name)))
            written[key] = str(dest)
            continue
        jobs.append((dest, storage_key))
        written[key] = str(dest)
    _download_all(storage, jobs)
    present = {}
    for key, path in written.items():
        target = Path(path)
        if target.is_file() or (target.is_dir() and any(target.iterdir())):
            present[key] = path
    return present


def _packet_file_jobs(state: object, capture_id: str, packet_dir: Path, packet_key: str) -> list[tuple[Path, str]]:
    packet: dict | None = None
    manifest = packet_dir / "packet.json"
    if manifest.is_file():
        try:
            loaded = json.loads(manifest.read_bytes())
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict):
            packet = loaded
    # Manifest paths are relative to the packet. A base packet sits at
    # captures/{capture_id}/packet.json, an amendment under its own prefix.
    base = packet_key.rsplit("packet.json", 1)[0] or f"captures/{capture_id}/"
    jobs: list[tuple[Path, str]] = []
    seen: set[str] = set()
    files = getattr(state, "files", None)
    records = files(capture_id) if callable(files) else []
    for rec in records:
        if getattr(rec, "state", None) != "committed":
            continue
        rel = _safe_rel(str(getattr(rec, "path", "") or ""))
        key = str(getattr(rec, "key", "") or "")
        if rel is None or not key:
            continue
        jobs.append((packet_dir / rel, key))
        seen.add(rel.as_posix())
    for rel_text in _packet_paths(packet):
        if rel_text in seen:
            continue
        rel = _safe_rel(rel_text)
        if rel is None:
            continue
        jobs.append((packet_dir / rel, f"{base}{rel.as_posix()}"))
        seen.add(rel.as_posix())
    return jobs


def _download_all(storage: object, jobs: list[tuple[Path, str]]) -> None:
    if not jobs:
        return
    if len(jobs) == 1:
        _download_one(storage, jobs[0])
        return
    workers = min(_DOWNLOAD_WORKERS, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda job: _download_one(storage, job), jobs))


def _download_one(storage: object, job: tuple[Path, str]) -> None:
    dest, key = job
    data = _read_optional(storage, key)
    if data is None:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _read_optional(storage: object, key: str) -> bytes | None:
    try:
        data = storage.get_bytes(key)  # type: ignore[attr-defined]
    except Exception as exc:
        if isinstance(exc, FileNotFoundError) or type(exc).__name__ in {"NotFound", "NotFoundError"}:
            return None
        raise
    return data if isinstance(data, bytes) else None


def _copy_packet_cache(cache_dir: Path | None, packet_dir: Path, *, include_files: bool) -> bool:
    if cache_dir is None or not (cache_dir / ".complete").is_file():
        return False
    manifest = cache_dir / "packet.json"
    if not manifest.is_file():
        return False
    if include_files:
        shutil.copytree(cache_dir, packet_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".complete"))
        return (packet_dir / "packet.json").is_file()
    shutil.copy2(manifest, packet_dir / "packet.json")
    return True


def _publish_packet_cache(packet_dir: Path, cache_dir: Path) -> None:
    if (cache_dir / ".complete").is_file():
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    for path in packet_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(packet_dir)
        dest = cache_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    (cache_dir / ".complete").write_bytes(b"ok")


def _packet_paths(packet: dict | None) -> list[str]:
    if not packet:
        return []
    paths: list[str] = []
    for item in packet.get("files") or []:
        if isinstance(item, dict) and item.get("path"):
            paths.append(str(item["path"]))
    return paths


def _safe_rel(path: str) -> Path | None:
    rel = Path(path)
    if not path or rel.is_absolute() or ".." in rel.parts:
        return None
    return rel
