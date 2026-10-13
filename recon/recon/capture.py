"""A capture: posed photos, optional LiDAR depth, and whatever the phone marked, in one form.

Every input is adapted here and nowhere else. The internal form:
- world: ARKit's `.gravity` frame, meters, +y up. For the app's scan bundle it is the scene frame
  in meters, so the ground at the wall is y = 0; for a Measure Lab session it is ARKit's own world.
- camera: ARKit axes (+x right, +y up in the unrotated sensor image, looking along -z);
  `cam_to_world` is 4x4.
- intrinsics: fx, fy, cx, cy in pixels of that image, with (0, 0) the image's top-left corner.

Readers:
- the app's scan bundle (`shareableScan` on t3/ios-mvf): `scene.json` (server/schemas/
  scene.schema.json, feet) and the keyframe JPEGs it names, as a folder or zip;
- a Measure Lab session, format 2 (experiments/measure-lab/README.md on t3/measure-lab), as a
  folder or zip. The ADVIO replay is one.

LiDAR depth, when a keyframe carries it, uses Measure Lab's layout: `depth: {file, confidenceFile,
w, h}`, Float32 meters and UInt8 confidence (0 low, 1 medium, 2 high), row-major, in the
keyframe's unrotated orientation.

Every file a manifest names must lie inside the manifest's folder (`inside`), and every keyframe id
must be a single safe file-name component (`frame_id`).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

FEET = 0.3048
UP = np.array([0.0, 1.0, 0.0])


@dataclass(frozen=True)
class LidarDepth:
    file: Path
    confidence: Path | None
    width: int
    height: int


@dataclass(frozen=True)
class Frame:
    id: str
    image: Path
    width: int
    height: int
    intrinsics: np.ndarray  # fx, fy, cx, cy
    cam_to_world: np.ndarray  # 4x4, meters, ARKit camera axes
    lidar: LidarDepth | None = None

    @property
    def center(self) -> np.ndarray:
        return self.cam_to_world[:3, 3]


@dataclass(frozen=True)
class WallHint:
    """A wall line the phone marked: a point on its foot, and horizontal unit vectors."""

    point: np.ndarray
    along: np.ndarray
    outward: np.ndarray


@dataclass
class Capture:
    source: str  # "scan-bundle" or "measure-lab"
    root: Path
    frames: list[Frame]
    ground_y: float | None  # world y of the ground at the wall, when the phone knows it
    meter: np.ndarray | None  # world position of the electric meter
    wall: WallHint | None
    scene: dict | None = None  # the bundle's scene.json, updated in place of rebuilt
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Depth maps and poses are looked up by id, while coverage walks every frame record, so a
        # repeated id would let one depth view count from two camera positions and fake two-view
        # coverage. Every capture is refused with one, however it was built.
        seen: set[str] = set()
        for f in self.frames:
            if f.id in seen:
                raise DuplicateFrameId(
                    f"keyframe id {f.id!r} appears more than once in {self.root}"
                )
            seen.add(f.id)


ROTATION_TOL = 1e-2  # scene.json rounds poses to 4 decimals; anything further off is not a pose

# Existing bundle admission policy in verification/hsverify/e2e.py, not measured capture maxima.
MAX_BUNDLE_ENTRIES = 2000
MAX_BUNDLE_BYTES = 512 * 2**20


def column_major(pose: list[float]) -> np.ndarray:
    """A 4x4 from 16 column-major numbers, its rotation snapped to the nearest proper rotation.
    Exported poses are rounded, so their rotations are orthonormal only to about 1e-4."""
    if len(pose) != 16:
        raise ValueError(f"pose has {len(pose)} numbers, expected 16 (column-major 4x4)")
    T = np.array(pose, dtype=np.float64).reshape(4, 4).T
    u, _, vt = np.linalg.svd(T[:3, :3])
    R = u @ vt
    if np.linalg.det(R) < 0 or np.abs(R - T[:3, :3]).max() > ROTATION_TOL:
        raise ValueError(f"pose rotation is not a rotation: {T[:3, :3].round(4).tolist()}")
    T[:3, :3] = R
    return T


def outward_of(along: np.ndarray) -> np.ndarray:
    """scene.schema.json: a baseline's outward side is its direction turned 90 degrees clockwise
    in plan seen from above (+y). In (x, z) that takes (ax, az) to (-az, ax)."""
    return np.array([-along[2], 0.0, along[0]])


def unpack(path: Path, work: Path) -> Path:
    """A folder as is; a zip extracted once under `work`, keyed by its sha256."""
    if path.is_dir():
        return path
    if path.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError(f"{path}: archive exceeds {MAX_BUNDLE_BYTES} bytes")
    with path.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()[:16]
    out = work / "bundles" / digest
    if not out.exists():
        _zip_directory_limits(path)
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > MAX_BUNDLE_ENTRIES:
                raise ValueError(f"{path}: {len(infos)} entries, limit {MAX_BUNDLE_ENTRIES}")
            if sum(i.file_size for i in infos) > MAX_BUNDLE_BYTES:
                raise ValueError(f"{path}: expanded archive exceeds {MAX_BUNDLE_BYTES} bytes")
            members = [i for i in infos if not i.filename.startswith("__MACOSX/")]
            for info in members:
                n = info.filename
                if n.startswith("/") or ".." in Path(n).parts:
                    raise ValueError(f"{path}: unsafe entry {n!r}")
            out.parent.mkdir(parents=True, exist_ok=True)
            # Extraction stays private until every CRC-checked member finishes. Failed extraction
            # cannot leave a partial cache directory that a later run mistakes for a complete one.
            stage = Path(tempfile.mkdtemp(dir=out.parent, prefix=f".{digest}-"))
            try:
                written = 0
                for info in members:
                    dest = stage / info.filename
                    if info.is_dir():
                        dest.mkdir(parents=True, exist_ok=True)
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, dest.open("wb") as dst:
                        while chunk := src.read(2**20):
                            written += len(chunk)
                            if written > MAX_BUNDLE_BYTES:
                                raise ValueError(f"{path}: expanded archive exceeds byte limit")
                            dst.write(chunk)
                try:
                    stage.rename(out)
                except OSError:
                    # Another run of this exact content may have finished first.
                    if not out.is_dir():
                        raise
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
    return out


def _zip_directory_limits(path: Path) -> None:
    """Check central-directory records in bounded reads before ZipFile allocates their metadata.
    ZIP64 and split archives exceed this worker's ordinary ZIP admission format and are refused.
    The record layouts are PKWARE APPNOTE sections 4.3.12 and 4.3.16."""
    with path.open("rb") as f:
        size = path.stat().st_size
        f.seek(max(0, size - 22 - 65535))
        tail = f.read(22 + 65535)
        end = len(tail)
        while True:
            pos = tail.rfind(b"PK\x05\x06", 0, end)
            if pos < 0:
                raise ValueError(f"{path}: missing ZIP end record")
            if pos + 22 <= len(tail):
                record = struct.unpack("<4s4H2IH", tail[pos : pos + 22])
                if pos + 22 + record[-1] == len(tail):
                    break
            end = pos
        _, disk, central_disk, disk_entries, total_entries, length, offset, _ = record
        if disk or central_disk or disk_entries != total_entries:
            raise ValueError(f"{path}: split ZIP archives are not supported")
        if total_entries > MAX_BUNDLE_ENTRIES:
            raise ValueError(f"{path}: {total_entries} entries, limit {MAX_BUNDLE_ENTRIES}")
        directory_end = offset + length
        end_offset = size - len(tail) + pos
        if length == 0xFFFFFFFF or offset == 0xFFFFFFFF or directory_end != end_offset:
            raise ValueError(f"{path}: ZIP64 or inconsistent ZIP directory")
        f.seek(offset)
        count, expanded = 0, 0
        while f.tell() < directory_end:
            header = f.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                raise ValueError(f"{path}: invalid ZIP central-directory record")
            fields = struct.unpack("<4s6H3I5H2I", header)
            count += 1
            expanded += fields[9]
            if count > MAX_BUNDLE_ENTRIES:
                raise ValueError(f"{path}: {count} entries, limit {MAX_BUNDLE_ENTRIES}")
            if expanded > MAX_BUNDLE_BYTES:
                raise ValueError(f"{path}: expanded archive exceeds {MAX_BUNDLE_BYTES} bytes")
            f.seek(sum(fields[10:13]), 1)
            if f.tell() > directory_end:
                raise ValueError(f"{path}: ZIP central-directory record exceeds directory")
        if count != total_entries:
            raise ValueError(f"{path}: inconsistent ZIP entry count")


def _find(root: Path, name: str) -> Path | None:
    hits = [p for p in root.rglob(name) if "__MACOSX" not in p.parts]
    if len(hits) > 1:
        raise ValueError(f"{root}: {len(hits)} copies of {name}; expected one")
    return hits[0] if hits else None


def load(path: Path, work: Path) -> Capture:
    root = unpack(path, work)
    scene = _find(root, "scene.json")
    session = _find(root, "session.json")
    if scene is not None:
        return _scan_bundle(scene.parent, json.loads(scene.read_text()))
    if session is not None:
        doc = json.loads(session.read_text())
        if doc.get("format") != "measure-lab-session" or doc.get("formatVersion") != 2:
            raise ValueError(f"{session}: not a Measure Lab session, format 2")
        return _measure_lab(session.parent, doc)
    raise ValueError(f"{path}: neither scene.json (app scan bundle) nor session.json (Measure Lab)")


class UnsafePath(ValueError):
    """A file the manifest names lies outside the manifest's folder."""


def inside(root: Path, ref: str, field: str) -> Path:
    """`root / ref`, refused unless it stays inside `root`. The manifest is untrusted: an
    absolute path, a `..` component or a symlink out of the folder would let a crafted packet read
    any local file, and an image's colours would then reach the returned model. The check runs
    on the resolved path, so a symlink is followed before it is judged; the path returned is the
    unresolved one, as the manifest names it."""
    rel = Path(ref)
    if rel.is_absolute() or ".." in rel.parts:
        raise UnsafePath(f"{field} {ref!r} must be a relative path inside {root}")
    base = root.resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        raise UnsafePath(f"{field} {ref!r} resolves to {target}, outside {base}")
    return root / rel


class BadFrameId(ValueError):
    """A keyframe id that is not a single safe file-name component."""


class DuplicateFrameId(ValueError):
    """Two keyframes share an id."""


FRAME_ID = re.compile(r"[A-Za-z0-9._-]+")


def frame_id(value: object) -> str:
    """A keyframe id, checked. The worker names cache files after ids (`depth.moge` writes
    `<id>.upright.jpg` and has the model write `<id>.moge2.npz`), so an id such as `/tmp/x` or
    `../../x` would write outside the cache. Only letters, digits, `.`, `_` and `-` are allowed,
    and not `.` or `..` alone."""
    if not isinstance(value, str) or not FRAME_ID.fullmatch(value) or value in (".", ".."):
        raise BadFrameId(
            f"keyframe id {value!r} must be letters, digits, '.', '_' or '-', and not '.' or '..'"
        )
    return value


def _lidar(root: Path, kf: dict) -> LidarDepth | None:
    d = kf.get("depth")
    if not d:
        return None
    file = inside(root, d["file"], "depth.file")
    conf = d.get("confidenceFile")
    conf_path = inside(root, conf, "depth.confidenceFile") if conf else None
    return LidarDepth(file, conf_path, int(d["w"]), int(d["h"]))


def _scan_bundle(root: Path, scene: dict) -> Capture:
    frames = []
    for kf in scene.get("keyframes", []):
        T = column_major(kf["pose"])
        T[:3, 3] *= FEET  # scene frame translations are feet
        frames.append(
            Frame(
                frame_id(kf["id"]),
                inside(root, kf["img"], "img"),
                int(kf["w"]),
                int(kf["h"]),
                np.array(kf["intrinsics"], dtype=np.float64),
                T,
                _lidar(root, kf),
            )
        )
    if not frames:
        raise ValueError(f"{root}/scene.json has no keyframes: nothing to reconstruct")
    meter = np.array(scene["meter"]["pos"], dtype=np.float64) * FEET
    wall = None
    walls = {w["id"]: w for w in scene["walls"]}
    base = np.array(walls[scene["meter"]["wall_id"]]["baseline"], dtype=np.float64) * FEET
    along = np.array([base[-1, 0] - base[0, 0], 0.0, base[-1, 1] - base[0, 1]])
    along /= np.linalg.norm(along)
    wall = WallHint(np.array([base[0, 0], 0.0, base[0, 1]]), along, outward_of(along))
    return Capture("scan-bundle", root, frames, 0.0, meter, wall, scene)


def _measure_lab(root: Path, doc: dict) -> Capture:
    for kf in doc["keyframes"]:
        frame_id(kf["id"])
    keyframes = sorted(doc["keyframes"], key=lambda k: k["id"])
    images = {kf["id"]: inside(root, kf["img"], "img") for kf in keyframes}
    frames = [
        Frame(
            kf["id"],
            images[kf["id"]],
            int(kf["w"]),
            int(kf["h"]),
            np.array(kf["intrinsics"], dtype=np.float64),
            column_major(kf["pose"]),
            _lidar(root, kf),
        )
        for kf in keyframes
        if images[kf["id"]].exists()  # a JPEG still being written at share time has no entry
    ]
    points = {p["id"]: np.array(p["position"], dtype=np.float64) for p in doc.get("points", [])}
    wall, ground = None, None
    for w in doc.get("walls", []):
        a, b = (points[c] for c in w["contacts"][:2])
        along = np.array([b[0] - a[0], 0.0, b[2] - a[2]])
        along /= np.linalg.norm(along)
        out = outward_of(along)
        cam = np.array(w["cameraPosition"], dtype=np.float64)
        if (cam - a) @ out < 0:  # the camera stood in front of the wall
            along, out = -along, -out
        wall, ground = WallHint(a.copy(), along, out), float((a[1] + b[1]) / 2)
    notes = []
    if wall is None:
        notes.append("the session marks no wall: the wall is found in the reconstruction")
    return Capture("measure-lab", root, frames, ground, None, wall, None, notes)
