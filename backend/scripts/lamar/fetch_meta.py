"""Fetch LaMAR LIN metadata and dump GT poses.

Rewritten from the A6 handoff (context dump a6-iphone-quality, section 3 step 1).
The original ``fetch_meta.py`` lived only on that VM.

``remotezip`` is a project data dependency. ``pycolmap`` is imported inside
``dump_gt``, which reads a reconstruction. Unit tests of member selection
and the manifest update do not need ``pycolmap``.

Default data directory for A6's ``lamar_io.py`` is ``~/lamar``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from remotezip import RemoteZip

LAMAR_BASE = "https://cvg-data.inf.ethz.ch/lamar"
# The three LIN phone sessions A6 scored. Other ios_ keys in metadata_phone.json are not fetched.
SESSIONS = (
    "ios_2022-06-17_10.43.29",
    "ios_2022-02-10_14.47.24",
    "ios_2021-06-02_11.12.32",
)
META_NAMES = ("trajectories.txt", "images.txt", "sensors.txt")
SPARSE_FILES = (
    "LIN/sparse/cameras.bin",
    "LIN/sparse/frames.bin",
    "LIN/sparse/rigs.bin",
    "LIN/sparse/images.bin",
    "LIN/sparse/points3D.bin",
    "LIN/pose_covariances.txt",
)


def session_zip_url(session: str) -> str:
    return f"{LAMAR_BASE}/raw/LIN/sessions/{session}.zip"


def colmap_zip_url() -> str:
    return f"{LAMAR_BASE}/colmap/LIN.zip"


def metadata_phone_url() -> str:
    return f"{LAMAR_BASE}/raw/LIN/metadata_phone.json"


def licence_ok(text: str) -> bool:
    return "cc by-sa 4.0" in text.lower()


def pick_meta_members(namelist: list[str], session: str) -> dict[str, str]:
    """Map a local filename to the zip member that holds it.

    Prefer a path that contains the session id, then the shortest path.
    Skip files under ``raw_data/images``.
    """
    chosen: dict[str, str] = {}
    for want in META_NAMES:
        candidates = [
            name for name in namelist if (name.endswith("/" + want) or name == want) and "raw_data/images" not in name
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda name: (0 if session in name else 1, len(name), name))
        chosen[want] = candidates[0]
    return chosen


def pose_record(image: object, cameras: object) -> dict:
    """``name -> {R, t, cam}`` from ``cam_from_world()``, as the A6 dump describes."""
    cam_from_world = image.cam_from_world
    rigid = cam_from_world() if callable(cam_from_world) else cam_from_world
    rotation = rigid.rotation
    matrix = rotation.matrix() if hasattr(rotation, "matrix") else rotation.to_matrix()
    rows = [[float(value) for value in row] for row in matrix]
    translation = [float(value) for value in rigid.translation]
    camera = cameras[image.camera_id]
    params = [float(value) for value in getattr(camera, "params", [])]
    return {
        "R": rows,
        "t": translation,
        "cam": {
            "id": int(image.camera_id),
            "model": str(getattr(camera, "model_name", getattr(camera, "model", ""))),
            "width": int(getattr(camera, "width", 0)),
            "height": int(getattr(camera, "height", 0)),
            "params": params,
        },
    }


def split_gt(images: object, cameras: object) -> tuple[dict, dict]:
    ios: dict = {}
    navvis: dict = {}
    iterator = images.items() if hasattr(images, "items") else ((image_id, images[image_id]) for image_id in images)
    for _image_id, image in iterator:
        name = str(image.name)
        record = pose_record(image, cameras)
        if name.startswith("ios_") or "/ios_" in name:
            ios[name] = record
        elif name.startswith("navvis_") or "/navvis_" in name:
            navvis[name] = record
    return ios, navvis


def dump_gt(sparse_dir: Path, out_dir: Path) -> tuple[int, int]:
    import pycolmap

    reconstruction = pycolmap.Reconstruction(str(sparse_dir))
    ios, navvis = split_gt(reconstruction.images, reconstruction.cameras)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gt_ios.json").write_text(json.dumps(ios))
    (out_dir / "gt_navvis.json").write_text(json.dumps(navvis))
    return len(ios), len(navvis)


def fingerprint(path: Path) -> tuple[str, int]:
    """SHA-256 and size. A directory fingerprint hashes ``relpath sha256`` lines."""
    if path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return digest, path.stat().st_size
    lines: list[str] = []
    total = 0
    for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        lines.append(f"{file_path.relative_to(path).as_posix()} {digest}")
        total += file_path.stat().st_size
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return digest, total


def update_fixture_manifest(manifest_path: Path, data_dir: Path, items: list[dict]) -> None:
    if manifest_path.is_file():
        document = json.loads(manifest_path.read_text())
    else:
        document = {"format": "hss-fixtures/v0", "items": []}
    by_id = {item["id"]: item for item in document.get("items", [])}
    for item in items:
        current = dict(item)
        relative = current.get("data_relative")
        if relative:
            target = data_dir / relative
            if target.exists():
                digest, size = fingerprint(target)
                current["sha256"] = digest
                current["bytes"] = size
        current.pop("data_relative", None)
        by_id[current["id"]] = current
    document["format"] = "hss-fixtures/v0"
    document["items"] = [by_id[key] for key in sorted(by_id)]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(document, indent=2) + "\n")


def lamar_manifest_items() -> list[dict]:
    """Fixture rows. ``data_relative`` is stripped before the file is written."""
    licence = "LaMAR LIN, CC BY-SA 4.0, internal evaluation, derived data share-alike"
    rows = [
        (
            "lamar:lin-metadata-phone",
            ".fixtures/lamar/metadata_phone.json",
            metadata_phone_url(),
            "metadata_phone.json",
        ),
        ("lamar:lin-colmap", ".fixtures/lamar/colmap", colmap_zip_url(), "colmap"),
        ("lamar:lin-gt-ios", ".fixtures/lamar/gt_ios.json", colmap_zip_url(), "gt_ios.json"),
        ("lamar:lin-gt-navvis", ".fixtures/lamar/gt_navvis.json", colmap_zip_url(), "gt_navvis.json"),
        (
            "lamar:lin-segments",
            ".fixtures/lamar/segments.json",
            "internal/breadth/a6-iphone-quality/results/segments.json",
            "segments.json",
        ),
    ]
    for session in SESSIONS:
        rows.append(
            (
                f"lamar:lin-frames-{session}",
                f".fixtures/lamar/raw/{session}",
                session_zip_url(session),
                f"raw/{session}",
            )
        )
    items = []
    for item_id, cache_path, source, relative in rows:
        items.append(
            {
                "id": item_id,
                "kind": "lamar",
                "repo_path": None,
                "cache_path": cache_path,
                "source": source,
                "bytes": None,
                "sha256": None,
                "committed": False,
                "licence": licence,
                "data_relative": relative,
            }
        )
    return items


def _extract_members(zip_url: str, members: dict[str, str], dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with RemoteZip(zip_url) as archive:
        for local_name, member in members.items():
            target = dest / local_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))


def gate_sessions(document: dict) -> tuple[list[str], list[str]]:
    """Split the three A6 sessions into those present in metadata and those absent."""
    keys = {str(key) for key in document}
    present = [session for session in SESSIONS if session in keys]
    missing = [session for session in SESSIONS if session not in keys]
    return present, missing


def fetch_sessions(data_dir: Path) -> list[str]:
    import urllib.request

    data_dir.mkdir(parents=True, exist_ok=True)
    meta_path = data_dir / "metadata_phone.json"
    with urllib.request.urlopen(metadata_phone_url(), timeout=120) as response:
        payload = response.read()
    meta_path.write_bytes(payload)
    document = json.loads(payload)
    if not isinstance(document, dict):
        raise SystemExit("metadata_phone.json is not an object of session ids")
    present, missing = gate_sessions(document)
    for session in present:
        url = session_zip_url(session)
        with RemoteZip(url) as archive:
            chosen = pick_meta_members(archive.namelist(), str(session))
        if set(chosen) != set(META_NAMES):
            missing.append(str(session))
            continue
        _extract_members(url, chosen, data_dir / "raw" / str(session))
    return missing


def fetch_colmap(data_dir: Path) -> Path:
    url = colmap_zip_url()
    with RemoteZip(url) as archive:
        names = set(archive.namelist())
        present = {name: name for name in SPARSE_FILES if name in names}
    if len(present) != len(SPARSE_FILES):
        found = ", ".join(sorted(present))
        raise SystemExit(f"colmap zip is missing expected members; found {found}")
    dest = data_dir / "colmap"
    _extract_members(url, present, dest)
    return dest / "LIN" / "sparse"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch LaMAR LIN metadata and write gt_ios.json")
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--write-manifest", type=Path, default=None)
    parser.add_argument(
        "--dump-only", action="store_true", help="Only write gt_ios.json from an existing colmap sparse model"
    )
    args = parser.parse_args(argv)
    data_dir = args.data.expanduser()
    if args.dump_only:
        ios_count, nav_count = dump_gt(data_dir / "colmap" / "LIN" / "sparse", data_dir)
        print(f"gt_ios {ios_count} gt_navvis {nav_count}")
    else:
        missing = fetch_sessions(data_dir)
        if missing:
            print("sessions missing trajectories/images/sensors: " + ", ".join(missing))
        sparse = fetch_colmap(data_dir)
        ios_count, nav_count = dump_gt(sparse, data_dir)
        print(f"gt_ios {ios_count} gt_navvis {nav_count}")
    if args.write_manifest is not None:
        update_fixture_manifest(args.write_manifest, data_dir, lamar_manifest_items())
        print(f"manifest {args.write_manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
