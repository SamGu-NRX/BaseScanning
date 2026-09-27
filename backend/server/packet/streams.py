"""CSV stream checks and the column reader."""

from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np

from server.packet.integrity import resolve_under
from server.packet.params import PacketParams
from server.packet.report import Bag, pointer


def open_stream(path: Path):
    return gzip.open(path, "rt", newline="") if path.suffix == ".gz" else path.open(newline="")


def read_table(path: Path) -> tuple[list[str], list[list[str]]] | None:
    try:
        with open_stream(path) as handle:
            header = handle.readline()
            if not header:
                return [], []
            columns = header.strip().split(",")
            rows = []
            for line in handle:
                if line.strip():
                    rows.append(line.strip().split(","))
        return columns, rows
    except Exception:
        return None


def check_streams(pk: dict, root: Path, bag: Bag, params: PacketParams) -> dict[str, dict]:
    """Return per-stream derived stats, including scaleToG."""
    derived: dict[str, dict] = {}
    streams = pk.get("streams") or {}
    if not isinstance(streams, dict):
        return derived
    listed = {
        entry.get("path")
        for entry in pk.get("files") or []
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }
    for name, stream in streams.items():
        if not isinstance(stream, dict):
            continue
        rel = stream.get("file")
        ptr = pointer("streams", name, "columns")
        if not isinstance(rel, str) or rel not in listed:
            continue
        full = resolve_under(root, rel)
        if full is None or not full.is_file():
            continue
        table = read_table(full)
        if table is None:
            bag.error("stream_unreadable", pointer("streams", name, "file"), f"{name} could not be read")
            continue
        header, rows = table
        if header != list(stream.get("columns") or []):
            bag.error("stream_header", ptr, f"{name} CSV header does not match columns")
            continue
        if not rows:
            bag.error("stream_empty", pointer("streams", name, "file"), f"{name} has no rows")
            continue
        if _malformed_cells(header, rows):
            bag.warn("stream_cell", pointer("streams", name, "file"), f"{name} has a cell that does not parse")
        stats = _stats(name, header, rows, stream, pk, bag, params)
        derived[name] = stats
    return derived


# Columns the capture spec stores as numbers. Text columns (epoch, tracking) are left alone.
_NUMERIC_COLUMNS = {
    "t",
    "ax",
    "ay",
    "az",
    "gx",
    "gy",
    "gz",
    "gravX",
    "gravY",
    "gravZ",
    "rotX",
    "rotY",
    "rotZ",
    "tx",
    "ty",
    "tz",
    "qx",
    "qy",
    "qz",
    "qw",
    "fx",
    "fy",
    "cx",
    "cy",
}


def _malformed_cells(header: list[str], rows: list[list[str]]) -> bool:
    """Ragged rows, NUL bytes, and non-numeric cells in numeric columns."""
    width = len(header)
    numeric = [index for index, name in enumerate(header) if name in _NUMERIC_COLUMNS]
    for row in rows:
        if len(row) != width or any("\x00" in cell for cell in row):
            return True
        for index in numeric:
            if index >= len(row):
                return True
            text = row[index].strip()
            try:
                value = float(text)
            except ValueError:
                return True
            if not np.isfinite(value):
                return True
    return False


def _column(header: list[str], rows: list[list[str]], name: str) -> np.ndarray | None:
    if name not in header:
        return None
    idx = header.index(name)
    values = []
    for row in rows:
        if idx >= len(row):
            return None
        try:
            values.append(float(row[idx]))
        except ValueError:
            values.append(np.nan)
    return np.asarray(values, dtype=float)


def _numeric_columns(header: list[str], rows: list[list[str]], names: tuple[str, ...]) -> list[np.ndarray] | None:
    axes: list[np.ndarray] = []
    for name in names:
        axis = _column(header, rows, name)
        if axis is None:
            return None
        axes.append(axis)
    return axes


def _stats(name, header, rows, stream, pk, bag: Bag, params: PacketParams) -> dict:
    times = _column(header, rows, "t")
    t_min = t_max = None
    rate = None
    if times is not None and np.all(np.isfinite(times)):
        if np.any(np.diff(times) < -1e-9):
            bag.warn("stream_time_order", pointer("streams", name, "file"), f"{name} timestamps go backwards")
        t_min = float(times[0])
        t_max = float(times[-1])
        deltas = np.diff(times)
        deltas = deltas[deltas > 0]
        if len(deltas):
            rate = float(1.0 / np.median(deltas))
            declared = stream.get("rateHz")
            if declared and rate < params.stream_rate_frac * float(declared):
                bag.warn("stream_rate_low", pointer("streams", name, "rateHz"), f"{name} measures {rate:.2f} Hz")
    scale = 1.0
    if name in {"imuRaw", "motion"}:
        scale = _imu_scale(name, header, rows, bag, params)
    if name == "arkitPoses" and times is not None:
        _arkit_gap(header, rows, times, pk, stream, bag, params)
        _arkit_quat(header, rows, bag, params)
    return {
        "rows": len(rows),
        "rateHzMeasured": rate,
        "tMin": t_min,
        "tMax": t_max,
        "scaleToG": scale,
    }


def _imu_scale(name: str, header, rows, bag: Bag, params: PacketParams) -> float:
    cols = ("ax", "ay", "az") if name == "imuRaw" else ("gravX", "gravY", "gravZ")
    if not all(c in header for c in cols):
        return 1.0
    axes = _numeric_columns(header, rows, cols)
    if axes is None:
        return 1.0
    mag = np.sqrt(sum(axis**2 for axis in axes))
    mag = mag[np.isfinite(mag)]
    if len(mag) == 0:
        return 1.0
    median = float(np.median(mag))
    if params.imu_g[0] <= median <= params.imu_g[1]:
        return 1.0
    if params.imu_ms2[0] <= median <= params.imu_ms2[1]:
        bag.warn("imu_units_m_s2", pointer("streams", name), f"{name} looks like m/s^2 ({median:.2f})")
        return 1.0 / params.standard_g
    bag.warn("imu_units_odd", pointer("streams", name), f"{name} median magnitude {median:.2f}")
    return 1.0


def _arkit_gap(header, rows, times, pk, stream, bag: Bag, params: PacketParams) -> None:
    needed = ["epoch", "tx", "ty", "tz"]
    if not all(c in header for c in needed):
        return
    rate = float(stream.get("rateHz") or 10.0)
    window = 2.0 / rate
    epochs = _text_column(header, rows, "epoch")
    tx = _column(header, rows, "tx")
    ty = _column(header, rows, "ty")
    tz = _column(header, rows, "tz")
    if tx is None or ty is None or tz is None:
        return
    missing = 0
    for frame in pk.get("keyframes") or []:
        pose = frame.get("pose")
        if not isinstance(pose, list) or len(pose) != 16:
            continue
        # column-major translation is indexes 12, 13, 14.
        # simd column-major: columns are contiguous, translation is the 4th column: indexes 12, 13, 14.
        center = np.array(pose[12:15], dtype=float)
        stamp = float(frame.get("timestamp", 0.0))
        eid = str(frame.get("epoch"))
        hit = False
        for i, t in enumerate(times):
            if epochs[i] != eid or abs(float(t) - stamp) > window:
                continue
            pos = np.asarray([float(tx[i]), float(ty[i]), float(tz[i])], dtype=np.float64)
            if float(np.linalg.norm(pos - center)) <= params.arkit_pose_pos_m:
                hit = True
                break
        if not hit:
            missing += 1
    if missing:
        bag.warn(
            "arkit_pose_stream_gap",
            pointer("streams", "arkitPoses"),
            f"{missing} keyframes have no matching arkitPoses row",
        )


def _arkit_quat(header, rows, bag: Bag, params: PacketParams) -> None:
    names = ["qx", "qy", "qz", "qw"]
    if not all(c in header for c in names):
        return
    axes = _numeric_columns(header, rows, tuple(names))
    if axes is None:
        return
    norms = np.sqrt(sum(axis**2 for axis in axes))
    if np.any(np.abs(norms - 1.0) > 1e-3):
        bag.warn("arkit_pose_stream_quat", pointer("streams", "arkitPoses"), "a pose quaternion is not unit length")


def _text_column(header, rows, name: str) -> list[str]:
    idx = header.index(name)
    return [row[idx] if idx < len(row) else "" for row in rows]


def read_stream(pk: dict, name: str, root: Path, scale_to_g: float = 1.0) -> dict[str, np.ndarray]:
    """Columns as arrays. Acceleration and gravity columns are multiplied by scaleToG."""
    stream = (pk.get("streams") or {}).get(name)
    if not isinstance(stream, dict):
        raise KeyError(name)
    full = resolve_under(root, stream["file"])
    if full is None:
        raise FileNotFoundError(stream["file"])
    table = read_table(full)
    if table is None:
        raise OSError(stream["file"])
    header, rows = table
    out: dict[str, np.ndarray] = {}
    scale_names = {"ax", "ay", "az", "gravX", "gravY", "gravZ"}
    for col in header:
        if _looks_numeric(header, rows, col):
            values = _column(header, rows, col)
            if values is not None and col in scale_names:
                values = values * scale_to_g
            out[col] = values if values is not None else np.array([], dtype=object)
        else:
            out[col] = np.array(_text_column(header, rows, col), dtype=object)
    return out


def _looks_numeric(header, rows, col: str) -> bool:
    if col in {"epoch", "tracking"}:
        return False
    idx = header.index(col)
    for row in rows[:8]:
        if idx >= len(row):
            return False
        try:
            float(row[idx])
        except ValueError:
            return False
    return True


def motion_gravity(pk: dict, root: Path) -> list[tuple[float, np.ndarray]] | None:
    stream = (pk.get("streams") or {}).get("motion")
    if not isinstance(stream, dict):
        return None
    full = resolve_under(root, stream.get("file", ""))
    if full is None or not full.is_file():
        return None
    table = read_table(full)
    if table is None:
        return None
    header, rows = table
    if not all(c in header for c in ("t", "gravX", "gravY", "gravZ")):
        return None
    times = _column(header, rows, "t")
    axes = _numeric_columns(header, rows, ("gravX", "gravY", "gravZ"))
    if times is None or axes is None:
        return None
    gx, gy, gz = axes
    return [
        (float(times[i]), np.asarray([float(gx[i]), float(gy[i]), float(gz[i])], dtype=np.float64))
        for i in range(len(rows))
    ]


_GYRO_COLUMNS = {
    "imuRaw": ("gx", "gy", "gz"),
    "motion": ("rotX", "rotY", "rotZ"),
}


def load_gyro_series(pk: dict, root: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """Read imuRaw once (else motion). Callers look up many timestamps in the returned series."""
    for name, columns in _GYRO_COLUMNS.items():
        series = _gyro_series(pk, root, name, columns)
        if series is not None:
            return series
    return None


def gyro_at(series: tuple[np.ndarray, np.ndarray] | None, timestamp: float, window: float) -> float | None:
    if series is None:
        return None
    times, magnitude = series
    if len(times) == 0:
        return None
    idx = int(np.argmin(np.abs(times - timestamp)))
    if abs(float(times[idx]) - timestamp) > window:
        return None
    return float(magnitude[idx])


def nearest_gyro(pk: dict, root: Path, timestamp: float, window: float) -> float | None:
    """Rotation-rate magnitude (rad/s) from imuRaw, else the motion stream, within `window` seconds."""
    return gyro_at(load_gyro_series(pk, root), timestamp, window)


def _gyro_series(
    pk: dict, root: Path, name: str, columns: tuple[str, str, str]
) -> tuple[np.ndarray, np.ndarray] | None:
    stream = (pk.get("streams") or {}).get(name)
    if not isinstance(stream, dict):
        return None
    full = resolve_under(root, stream.get("file", ""))
    if full is None or not full.is_file():
        return None
    table = read_table(full)
    if table is None:
        return None
    header, rows = table
    if not all(c in header for c in ("t", *columns)):
        return None
    times = _column(header, rows, "t")
    ax = _column(header, rows, columns[0])
    ay = _column(header, rows, columns[1])
    az = _column(header, rows, columns[2])
    if times is None or ax is None or ay is None or az is None or len(times) == 0:
        return None
    return times, np.hypot(ax, np.hypot(ay, az))
