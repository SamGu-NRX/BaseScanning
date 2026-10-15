"""Read the packet fields this stage uses.

WS-02's ``NormalizedPacket`` is not on main yet. The loader accepts a v0.4
packet plus the derived block ``ext.hss.derived`` when it is present, and
recomputes flow / branch / anchor epoch when it is not (brief §4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from server.common.geometry import from_column_major
from server.poses.errors import PosesError

CLOSEUPS = ("meter_close", "meter_oblique")


@dataclass
class Frame:
    id: str
    name: str
    src: Path
    w: int
    h: int
    fx: float
    fy: float
    cx: float
    cy: float
    pose: np.ndarray | None
    timestamp: float
    tracking: str
    epoch: str
    purpose: str | None
    kind: str
    exposure_s: float | None
    intrinsics_source: str | None
    intrinsics_assumed: bool
    has_depth: bool
    meter: bool = False
    closeup: str | None = None
    flags: tuple[str, ...] = ()


@dataclass
class PacketView:
    packet_id: str
    tier: str
    root: Path
    frames: list[Frame]
    flow: str
    branch: str
    primary_epoch: str | None
    meter_source: str
    meter_ids: list[str] = field(default_factory=list)
    imu_path: Path | None = None
    warnings: list[str] = field(default_factory=list)


def _need(obj: dict, name: str):
    if name not in obj:
        raise PosesError(f"packet_field_missing:{name}")
    return obj[name]


def _resolve(root: Path, rel: str) -> Path:
    path = Path(rel)
    candidates = [root / path, root / path.name]
    for cand in candidates:
        if cand.is_file():
            return cand
    raise PosesError(f"image_unreadable:{rel}")


def _intrinsics(obj: dict, label: str) -> tuple[float, float, float, float]:
    k = obj.get("intrinsics")
    if not isinstance(k, list) or len(k) != 4:
        raise PosesError(f"packet_field_missing:{label}.intrinsics")
    return tuple(float(v) for v in k)  # type: ignore[return-value]


def _pose(obj: dict) -> np.ndarray | None:
    raw = obj.get("pose")
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) != 16:
        raise PosesError("packet_field_missing:pose")
    return from_column_major(raw)


def _unique_name(used: set[str], frame_id: str, img: str) -> str:
    base = Path(img).name
    if base not in used:
        used.add(base)
        return base
    name = f"{frame_id}__{base}"
    used.add(name)
    return name


def _stream_path(packet: dict, root: Path, role: str) -> Path | None:
    streams = packet.get("streams") or {}
    entry = streams.get(role) or {}
    rel = entry.get("file") or entry.get("path")
    if isinstance(rel, str):
        path = root / rel
        if path.is_file():
            return path
    for item in packet.get("files") or []:
        if item.get("role") == "stream" and role in str(item.get("path", "")):
            path = root / item["path"]
            if path.is_file():
                return path
    return None


def load_packet(
    packet_path: Path,
    *,
    meter_candidates: list[str] | None = None,
    meter_override: list[str] | None = None,
    max_frames: int | None = None,
) -> PacketView:
    packet_path = Path(packet_path)
    packet = json.loads(packet_path.read_text())
    root = packet_path.parent
    packet_id = str(_need(packet, "packetId"))
    session = _need(packet, "session")
    tier = str(_need(session, "tier"))
    keyframes = list(packet.get("keyframes") or [])
    stills = list(packet.get("stills") or [])
    if max_frames is not None:
        keyframes = keyframes[:max_frames]

    used: set[str] = set()
    frames: list[Frame] = []
    by_id: dict[str, Frame] = {}
    for kf in keyframes:
        img = str(_need(kf, "img"))
        fx, fy, cx, cy = _intrinsics(kf, "keyframe")
        depth = kf.get("depth") or {}
        frame = Frame(
            id=str(_need(kf, "id")),
            name=_unique_name(used, str(kf.get("id", "k")), img),
            src=_resolve(root, img),
            w=int(_need(kf, "w")),
            h=int(_need(kf, "h")),
            fx=fx,
            fy=fy,
            cx=cx,
            cy=cy,
            pose=_pose(kf),
            timestamp=float(kf.get("timestamp") or 0.0),
            tracking=str(kf.get("tracking") or "normal"),
            epoch=str(kf.get("epoch") or ""),
            purpose=kf.get("purpose"),
            kind="keyframe",
            exposure_s=_exposure(kf),
            intrinsics_source=kf.get("intrinsicsSource") or "arkit",
            intrinsics_assumed=bool(kf.get("intrinsicsAssumed")),
            has_depth=bool(depth.get("file") and depth.get("confidenceFile")),
            flags=_flags(packet, str(kf.get("id")), kf),
        )
        frames.append(frame)
        by_id[frame.id] = frame

    guided = bool(frames) and all(f.pose is not None for f in frames)
    photo_only = tier == "photo_only" or not guided
    warnings: list[str] = []

    def add_still(st: dict, *, force: bool) -> None:
        purpose = st.get("purpose")
        if not force and purpose not in CLOSEUPS and photo_only is False:
            return
        if photo_only or purpose in CLOSEUPS or force:
            img = str(_need(st, "img"))
            if "intrinsics" not in st and st.get("keyframe") in by_id:
                src_k = by_id[st["keyframe"]]
                k = (src_k.fx, src_k.fy, src_k.cx, src_k.cy)
                w, h = src_k.w, src_k.h
                pose = src_k.pose
                source = "arkit"
            else:
                k = _intrinsics(st, "still")
                w, h = int(_need(st, "w")), int(_need(st, "h"))
                pose = _pose(st)
                source = st.get("intrinsicsSource")
            assumed = bool(st.get("intrinsicsAssumed")) or (
                source == "exif_focal35" and st.get("intrinsicsAssumed", True) is not False
            )
            if source == "exif_focal35" and st.get("intrinsicsAssumed"):
                warnings.append(f"focal_assumed:{st.get('id')}")
            frame = Frame(
                id=str(_need(st, "id")),
                name=_unique_name(used, str(st.get("id", "s")), img),
                src=_resolve(root, img),
                w=w,
                h=h,
                fx=k[0],
                fy=k[1],
                cx=k[2],
                cy=k[3],
                pose=pose,
                timestamp=float(st.get("timestamp") or 0.0),
                tracking="notAvailable",
                epoch=str(st.get("epoch") or ""),
                purpose=purpose,
                kind="still",
                exposure_s=_exposure(st),
                intrinsics_source=source,
                intrinsics_assumed=bool(assumed) if source == "exif_focal35" else bool(st.get("intrinsicsAssumed")),
                has_depth=False,
                closeup=purpose if purpose in CLOSEUPS else None,
            )
            if frame.id in by_id:
                return
            frames.append(frame)
            by_id[frame.id] = frame

    if photo_only:
        for st in stills:
            add_still(st, force=True)
    else:
        for st in stills:
            if st.get("purpose") in CLOSEUPS and st.get("keyframe") not in by_id:
                add_still(st, force=True)

    if len(frames) < 3:
        raise PosesError("too_few_frames")

    derived = ((packet.get("ext") or {}).get("hss") or {}).get("derived") or {}
    if guided:
        flow = str(derived.get("flow") or "guided")
    else:
        flow = str(derived.get("flow") or "photo")
    branch = str(derived.get("branch") or ("lidar" if any(f.has_depth for f in frames) else "image"))

    meter_ids, meter_source = _meter(packet, by_id, meter_candidates, meter_override)
    for fid in meter_ids:
        if fid in by_id:
            by_id[fid].meter = True
    for frame in frames:
        if frame.purpose in CLOSEUPS:
            frame.closeup = frame.purpose
    # Mark keyframes linked from close-up stills.
    for st in stills:
        if st.get("purpose") in CLOSEUPS and st.get("keyframe") in by_id:
            by_id[st["keyframe"]].closeup = st["purpose"]

    imu = _stream_path(packet, root, "motion") or _stream_path(packet, root, "imuRaw")
    epochs = [f.epoch for f in frames if f.kind == "keyframe" and f.epoch]
    primary = derived.get("primaryEpoch")
    if not primary and epochs:
        if meter_ids:
            for fid in meter_ids:
                if fid in by_id and by_id[fid].epoch:
                    primary = by_id[fid].epoch
                    break
        if not primary:
            counts: dict[str, int] = {}
            for ep in epochs:
                counts[ep] = counts.get(ep, 0) + 1
            primary = max(counts, key=counts.get)
    return PacketView(
        packet_id=packet_id,
        tier=tier,
        root=root,
        frames=frames,
        flow="guided" if flow == "guided" and guided else "photo",
        branch=branch,
        primary_epoch=str(primary) if primary else None,
        meter_source=meter_source,
        meter_ids=meter_ids,
        imu_path=imu,
        warnings=warnings,
    )


def _flags(packet: dict, frame_id: str, obj: dict) -> tuple[str, ...]:
    raw = obj.get("flags")
    if not isinstance(raw, list):
        block = ((packet.get("ext") or {}).get("hss") or {}).get("flags") or {}
        raw = block.get(frame_id) if isinstance(block, dict) else None
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw)


def _exposure(obj: dict) -> float | None:
    exp = obj.get("exposure") or {}
    dur = exp.get("duration")
    if dur is None:
        return None
    return float(dur)


def _meter(packet, by_id, candidates, override) -> tuple[list[str], str]:
    taps = packet.get("taps") or []
    for tap in taps:
        if tap.get("label") == "meter" and tap.get("keyframe") in by_id:
            return [str(tap["keyframe"])], "tap"
    scale = packet.get("scaleReference") or {}
    close = (scale.get("meterCloseUp") or {}).get("still")
    if close:
        if close in by_id:
            frame = by_id[close]
            if frame.kind == "still":
                return [frame.id], "closeup_link"
            return [frame.id], "closeup_link"
        for st in packet.get("stills") or []:
            if st.get("id") == close and st.get("keyframe") in by_id:
                return [str(st["keyframe"])], "closeup_link"
    if candidates:
        found = [c for c in candidates if c in by_id]
        if found:
            return found, "detection"
    if override:
        found = [c for c in override if c in by_id]
        if found:
            return found, "override"
    return [], "none"
