"""Stage work dirs and published bytes.

Artifact keys are the WS-00 §3.9 table, plus every other path a stage returns.
A stage reads ``work_dir/<key>``. The capture packet and committed files live in ``packet_dir`` (SD-17).
"""

from __future__ import annotations

import json
from pathlib import Path

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


def hydrate_packet(storage: object, state: object, capture_id: str, packet_dir: Path) -> None:
    """Copy ``captures/<id>/packet.json`` and the capture's committed files into ``packet_dir``."""

    packet_key = f"captures/{capture_id}/packet.json"
    packet: dict | None = None
    if storage.exists(packet_key):  # type: ignore[attr-defined]
        data = storage.get_bytes(packet_key)  # type: ignore[attr-defined]
        (packet_dir / "packet.json").write_bytes(data)
        try:
            loaded = json.loads(data)
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict):
            packet = loaded
    seen: set[str] = set()
    files = getattr(state, "files", None)
    records = files(capture_id) if callable(files) else []
    for rec in records:
        if getattr(rec, "state", None) != "committed":
            continue
        rel = _safe_rel(str(getattr(rec, "path", "") or ""))
        key = str(getattr(rec, "key", "") or "")
        if rel is None or not key or not storage.exists(key):  # type: ignore[attr-defined]
            continue
        _write_rel(storage, packet_dir, rel, key)
        seen.add(rel.as_posix())
    for rel_text in _packet_paths(packet):
        if rel_text in seen:
            continue
        rel = _safe_rel(rel_text)
        if rel is None:
            continue
        key = f"captures/{capture_id}/{rel.as_posix()}"
        if storage.exists(key):  # type: ignore[attr-defined]
            _write_rel(storage, packet_dir, rel, key)


def hydrate_artifacts(storage: object, dest_root: Path, artifacts: dict[str, str]) -> dict[str, str]:
    """Download every safe path a stage returned, including every §3.9 name."""

    written: dict[str, str] = {}
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
            if not names and storage.exists(storage_key):  # type: ignore[attr-defined]
                names = [storage_key]
            for name in names:
                if not str(name).startswith(prefix):
                    continue
                target = dest / str(name)[len(prefix) :]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(storage.get_bytes(name))  # type: ignore[attr-defined]
            written[key] = str(dest)
            continue
        if not storage.exists(storage_key):  # type: ignore[attr-defined]
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(storage.get_bytes(storage_key))  # type: ignore[attr-defined]
        written[key] = str(dest)
    return written


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


def _write_rel(storage: object, root: Path, rel: Path, key: str) -> None:
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(storage.get_bytes(key))  # type: ignore[attr-defined]
