"""Packet and pose reads. A directory resolves to ``packet.json`` only (SD-43)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from server.common.geometry import from_column_major
from server.scale.apply import CameraRecord


class ScaleError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def resolve_packet(path: Path) -> Path:
    """Name the manifest explicitly. A folder means ``packet.json``, never a glob."""

    target = Path(path)
    if target.is_dir():
        target = target / "packet.json"
    if not target.is_file():
        raise ScaleError("packet_field_missing:packet.json")
    return target


def load_packet(packet: Any, root: Path | None = None) -> tuple[dict, Path]:
    if isinstance(packet, (str, Path)):
        path = resolve_packet(Path(packet))
        payload = json.loads(path.read_text())
        base = root if root is not None else path.parent
        return payload, base
    if isinstance(packet, dict):
        if root is None:
            raise ScaleError("packet_field_missing:root")
        return packet, root
    dumped = getattr(packet, "model_dump", None)
    if dumped is not None:
        if root is None:
            raise ScaleError("packet_field_missing:root")
        return dumped(), root
    raise ScaleError("packet_field_missing:packet")


def require(packet: dict, name: str) -> Any:
    current: Any = packet
    for part in name.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ScaleError(f"packet_field_missing:{name}")
        current = current[part]
    return current


def load_cameras(poses_dir: Path) -> list[CameraRecord]:
    path = poses_dir / "cameras.json"
    if not path.is_file():
        raise ScaleError("poses_artifact_missing:cameras.json")
    payload = json.loads(path.read_text())
    cameras = []
    for row in payload:
        pose = from_column_major(row["pose"]) if _is_flat(row["pose"]) else np.asarray(row["pose"], float)
        cameras.append(
            CameraRecord(
                image=str(row["image"]),
                keyframe_id=row.get("keyframe_id"),
                pose=pose,
                intrinsics=[float(v) for v in row["intrinsics"]],
                w=int(row["w"]),
                h=int(row["h"]),
            )
        )
    return cameras


def load_registration(poses_dir: Path) -> dict:
    path = poses_dir / "registration_report.json"
    if not path.is_file():
        raise ScaleError("poses_artifact_missing:registration_report.json")
    return json.loads(path.read_text())


def load_points(poses_dir: Path) -> np.ndarray:
    path = poses_dir / "sparse.ply"
    if not path.is_file():
        return np.zeros((0, 3))
    return read_ply_xyz(path)


def load_tracks(poses_dir: Path) -> dict[str, np.ndarray]:
    """Per-image ``(N, 3)`` points.

    WS-04 writes CSR ``indptr`` / ``indices`` / ``point_ids`` (cameras.json order,
    sparse.ply row order). Older archives store one array per image name.
    """

    path = poses_dir / "tracks.npz"
    if not path.is_file():
        return {}
    archive = np.load(path, allow_pickle=False)
    names = set(archive.files)
    if {"indptr", "indices", "point_ids"} <= names:
        return _tracks_from_csr(poses_dir, archive)
    out: dict[str, np.ndarray] = {}
    for key in archive.files:
        out[str(key)] = np.asarray(archive[key], float)
    return out


# Packed little-endian vertex properties. A list property or an unknown type
# yields an empty cloud so a mis-strided body cannot become a verified scale.
_PLY_DTYPES = {
    "char": "i1",
    "int8": "i1",
    "uchar": "u1",
    "uint8": "u1",
    "short": "<i2",
    "int16": "<i2",
    "ushort": "<u2",
    "uint16": "<u2",
    "int": "<i4",
    "int32": "<i4",
    "uint": "<u4",
    "uint32": "<u4",
    "float": "<f4",
    "float32": "<f4",
    "double": "<f8",
    "float64": "<f8",
}


def read_ply_xyz(path: Path) -> np.ndarray:
    """Vertex xyz from an ASCII or binary little-endian PLY, using the header's property list."""

    empty = np.zeros((0, 3))
    data = path.read_bytes()
    header, sep, rest = data.partition(b"end_header\n")
    if not sep:
        return empty
    count, props = _vertex_properties(header)
    if count is None or props is None or count < 0:
        return empty
    names = [name for name, _typ in props]
    if not {"x", "y", "z"} <= set(names):
        return empty
    if b"format ascii" in header:
        return _ascii_xyz(rest, names, count)
    if b"format binary_little_endian" not in header:
        return empty
    fields: list[tuple[str, str]] = []
    for name, typ in props:
        dtype = _PLY_DTYPES.get(typ)
        if dtype is None:
            return empty
        fields.append((name, dtype))
    if count == 0:
        return empty
    dt = np.dtype(fields)
    need = count * dt.itemsize
    if len(rest) < need:
        return empty
    arr = np.frombuffer(rest[:need], dtype=dt, count=count)
    return np.column_stack((np.asarray(arr["x"], float), np.asarray(arr["y"], float), np.asarray(arr["z"], float)))


def _vertex_properties(header: bytes) -> tuple[int | None, list[tuple[str, str]] | None]:
    count: int | None = None
    props: list[tuple[str, str]] = []
    in_vertex = False
    text = header.decode("ascii", errors="replace")
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] == "comment":
            continue
        if parts[0] == "element" and len(parts) >= 3:
            in_vertex = parts[1] == "vertex"
            if in_vertex:
                try:
                    count = int(parts[2])
                except ValueError:
                    return None, None
                props = []
            continue
        if not in_vertex or parts[0] != "property":
            continue
        if len(parts) < 3 or parts[1] == "list":
            return None, None
        props.append((parts[-1], parts[1]))
    return count, props


def _ascii_xyz(rest: bytes, names: list[str], count: int) -> np.ndarray:
    empty = np.zeros((0, 3))
    if count == 0:
        return empty
    ix, iy, iz = names.index("x"), names.index("y"), names.index("z")
    rows: list[tuple[float, float, float]] = []
    for line in rest.decode("ascii", errors="replace").splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) <= max(ix, iy, iz):
            return empty
        try:
            rows.append((float(parts[ix]), float(parts[iy]), float(parts[iz])))
        except ValueError:
            return empty
        if len(rows) == count:
            break
    if len(rows) != count:
        return empty
    return np.asarray(rows, float)


def _tracks_from_csr(poses_dir: Path, archive) -> dict[str, np.ndarray]:
    """Expand WS-04 CSR tracks into one ``(N, 3)`` cloud per ``cameras.json`` image."""

    cameras_path = poses_dir / "cameras.json"
    points = load_points(poses_dir)
    if not cameras_path.is_file() or len(points) == 0:
        return {}
    cameras = json.loads(cameras_path.read_text())
    if not isinstance(cameras, list) or not cameras:
        return {}
    indptr = np.asarray(archive["indptr"], np.int64).reshape(-1)
    indices = np.asarray(archive["indices"], np.int64).reshape(-1)
    point_ids = np.asarray(archive["point_ids"]).reshape(-1)
    n = len(points)
    if len(indptr) != n + 1 or len(point_ids) != n:
        return {}
    if int(indptr[0]) != 0 or int(indptr[-1]) != len(indices) or np.any(np.diff(indptr) < 0):
        return {}
    counts = np.diff(indptr)
    point_index = np.repeat(np.arange(n, dtype=np.int64), counts)
    if len(point_index) != len(indices):
        return {}
    valid = (indices >= 0) & (indices < len(cameras))
    if not np.any(valid):
        return {}
    cam_ids = indices[valid]
    observed = points[point_index[valid]]
    order = np.argsort(cam_ids, kind="mergesort")
    cam_ids = cam_ids[order]
    observed = observed[order]
    unique, starts = np.unique(cam_ids, return_index=True)
    ends = np.append(starts[1:], len(cam_ids))
    out: dict[str, np.ndarray] = {}
    for cam_i, start, end in zip(unique, starts, ends, strict=True):
        row = cameras[int(cam_i)]
        image = row.get("image") if isinstance(row, dict) else None
        if not image:
            continue
        cloud = np.asarray(observed[int(start) : int(end)], float)
        key = str(image)
        out[key] = np.concatenate([out[key], cloud]) if key in out else cloud
    return out


def _is_flat(pose) -> bool:
    return isinstance(pose, list) and pose and not isinstance(pose[0], list)
