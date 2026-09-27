"""Checklist plus pose-frusta coverage. Debounced to one run per 2 seconds."""

from __future__ import annotations

import json
import math
from typing import Any, get_args

from server.contracts.api import CaptureCheckData, HintData
from server.contracts.capture_check import CaptureCheckCode
from server.contracts.jobs import CommittedFile
from server.orchestrator.clock import parse_iso
from server.orchestrator.early.index import remember_early
from server.orchestrator.events import emit_capture_check, emit_hint
from server.orchestrator.services import Services
from server.orchestrator.stateio import get_json, put_json

CAPTURE_CHECK_CODES = get_args(CaptureCheckCode)

_STATION_STEP_M = 0.5
_REACH_M = 7.92
_GROUND_BELOW_M = 1.4
_METER_AHEAD_M = 1.0


def final_check(services: Services, capture_id: str, packet: dict[str, Any]) -> None:
    missing = _packet_missing(packet)
    _publish(services, capture_id, missing, meter_read="absent", frames=0)


def on_commit(services: Services, capture_id: str, files: list[CommittedFile]) -> None:
    """Remember every commit, then publish at most once per two seconds."""

    frames = _frames(services, capture_id)
    frames.extend(_frame_from_file(item) for item in files)
    _store_frames(services, capture_id, frames)
    if _debounced(services, capture_id):
        put_json(services.state, f"capture_check_pending:{capture_id}", {"pending": True})  # type: ignore[attr-defined]
        return
    _publish_frames(services, capture_id, frames)


def flush_pending(services: Services, capture_id: str) -> None:
    """Publish the walk so far when commits arrived inside the debounce window."""

    pending = get_json(services.state, f"capture_check_pending:{capture_id}")  # type: ignore[attr-defined]
    if not isinstance(pending, dict) or not pending.get("pending"):
        return
    _publish_frames(services, capture_id, _frames(services, capture_id))


def _frames(services: Services, capture_id: str) -> list[dict[str, Any]]:
    raw = get_json(services.state, f"capture_check_frames:{capture_id}")  # type: ignore[attr-defined]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _store_frames(services: Services, capture_id: str, frames: list[dict[str, Any]]) -> None:
    put_json(services.state, f"capture_check_frames:{capture_id}", frames)  # type: ignore[attr-defined]


def _frame_from_file(item: CommittedFile) -> dict[str, Any]:
    meta = item.meta or {}
    frame = meta.get("keyframe") or meta.get("meta")
    # A still's `keyframe` can be an id, rather than an embedded frame record.
    body = dict(frame) if isinstance(frame, dict) else dict(meta)
    body.setdefault("path", item.path)
    return body


def _debounced(services: Services, capture_id: str) -> bool:
    last = get_json(services.state, f"capture_check_at:{capture_id}")  # type: ignore[attr-defined]
    if not last:
        return False
    age = (services.clock.now() - parse_iso(str(last))).total_seconds()
    return age < 2


def _publish_frames(services: Services, capture_id: str, frames: list[dict[str, Any]]) -> None:
    missing = frusta_missing(frames)
    _publish(services, capture_id, missing, meter_read=_meter_read(services, capture_id), frames=len(frames))
    put_json(services.state, f"capture_check_at:{capture_id}", services.clock.iso())  # type: ignore[attr-defined]
    put_json(services.state, f"capture_check_pending:{capture_id}", {"pending": False})  # type: ignore[attr-defined]


def frusta_missing(frames: list[dict[str, Any]]) -> list[str]:
    """Wall and ground stations from committed keyframe poses. Empty input adds nothing."""
    posed = [frame for frame in frames if _pose(frame) is not None]
    if not posed:
        return []
    meter_frame = next((frame for frame in posed if _purpose(frame) == "meter_close"), posed[0])
    origin, forward, _right, _up = _axes(_pose(meter_frame))
    meter = _add(origin, _scale(forward, _METER_AHEAD_M))
    normal = _horizontal(forward)
    if _norm(normal) < 1e-6:
        return ["wall_left_of_meter", "wall_right_of_meter"]
    normal = _unit(normal)
    tangent = _unit((-normal[2], 0.0, normal[0]))
    ground_y = _axes(_pose(posed[0]))[0][1] - _GROUND_BELOW_M
    stations = []
    s = -_REACH_M
    while s <= _REACH_M + 1e-6:
        point = _add(meter, _scale(tangent, s))
        point = (point[0], ground_y, point[2])
        stations.append((s, point))
        s += _STATION_STEP_M
    missing: list[str] = []
    if _gap(stations, posed, normal, side=-1):
        missing.append("wall_left_of_meter")
        missing.append("wall_gap_left")
    if _gap(stations, posed, normal, side=1):
        missing.append("wall_right_of_meter")
        missing.append("wall_gap_right")
    if _ground_unseen(posed, side=-1):
        missing.append("ground_downward_left")
        missing.append("ground_not_seen_left")
    if _ground_unseen(posed, side=1):
        missing.append("ground_downward_right")
        missing.append("ground_not_seen_right")
    return missing


def _publish(
    services: Services,
    capture_id: str,
    missing: list[str],
    *,
    meter_read: str,
    frames: int = 0,
) -> None:
    codes = [code for code in missing if code in CAPTURE_CHECK_CODES]
    body = CaptureCheckData(complete=not codes, missing=codes, meterRead=meter_read)  # type: ignore[arg-type]
    emit_capture_check(services.state, capture_id, body, clock=services.clock)
    for code in missing:
        if code not in {"wall_gap_left", "wall_gap_right", "ground_not_seen_left", "ground_not_seen_right"}:
            continue
        if _hint_seen(services, capture_id, code):
            continue
        emit_hint(
            services.state,
            capture_id,
            HintData(code=code, message="Keep walking so this side of the meter is in frame."),  # type: ignore[arg-type]
            clock=services.clock,
        )
        _remember_hint(services, capture_id, code)
    stored = {"missing": missing, "at": services.clock.iso(), "complete": not codes, "frames": frames}
    check_key = f"early:{capture_id}:capture_check"
    put_json(services.state, check_key, stored)  # type: ignore[attr-defined]
    remember_early(services.state, capture_id, check_key)
    services.storage.put_bytes(  # type: ignore[attr-defined]
        f"captures/{capture_id}/early/capture_check.json",
        json.dumps(stored).encode(),
        "application/json",
    )


def _hint_seen(services: Services, capture_id: str, code: str) -> bool:
    seen = get_json(services.state, f"capture_check_hints:{capture_id}")  # type: ignore[attr-defined]
    return isinstance(seen, list) and code in seen


def _remember_hint(services: Services, capture_id: str, code: str) -> None:
    seen = get_json(services.state, f"capture_check_hints:{capture_id}")  # type: ignore[attr-defined]
    codes = [item for item in seen if isinstance(item, str)] if isinstance(seen, list) else []
    if code not in codes:
        codes.append(code)
    put_json(services.state, f"capture_check_hints:{capture_id}", codes)  # type: ignore[attr-defined]


def _packet_missing(packet: dict[str, Any]) -> list[str]:
    stills = packet.get("stills") or []
    purposes = {_purpose(still) for still in stills}
    missing = [
        code
        for code in ("meter_close", "meter_oblique", "panel_close", "panel_open", "main_disconnect")
        if code not in purposes
    ]
    taps = packet.get("taps") or []
    if not any((tap.get("element") == "meter") or (tap.get("id") == "meter") for tap in taps):
        missing.append("meter_tap")
    ext = (packet.get("scaleReference") or {}).get("ext") or {}
    door_on = ext.get("doorOnMeterSide", True)
    door_views = ext.get("doorViews") or []
    if door_on is not False and not door_views:
        missing.append("door_view_meter_side")
    return missing


def _meter_read(services: Services, capture_id: str) -> str:
    blob = get_json(services.state, f"early:{capture_id}:meter_identity")  # type: ignore[attr-defined]
    if not blob:
        return "absent" if services.settings.VISION_EXTERNAL_CALLS == "off" else "pending"
    return "ok" if blob.get("meter_number") else "unreadable"


def _purpose(frame: dict[str, Any]) -> str:
    return str(frame.get("purpose") or (frame.get("keyframe") or {}).get("purpose") or "")


def _pose(frame: dict[str, Any]) -> list[float] | None:
    pose = frame.get("pose") or (frame.get("camera") or {}).get("pose")
    if isinstance(pose, list) and len(pose) == 16:
        return [float(v) for v in pose]
    return None


def _axes(pose: list[float]) -> tuple[tuple[float, float, float], ...]:
    origin = (pose[12], pose[13], pose[14])
    right = (pose[0], pose[1], pose[2])
    up = (pose[4], pose[5], pose[6])
    forward = (-pose[8], -pose[9], -pose[10])
    return origin, forward, right, up


def _gap(
    stations: list[tuple[float, tuple[float, float, float]]],
    frames: list[dict[str, Any]],
    normal: tuple[float, float, float],
    *,
    side: int,
) -> bool:
    unseen = 0
    for position, point in stations:
        if side < 0 and position >= 0:
            continue
        if side > 0 and position <= 0:
            continue
        if _seen(point, frames, normal):
            unseen = 0
        else:
            unseen += 1
            if unseen >= 2:
                return True
    return False


def _seen(point: tuple[float, float, float], frames: list[dict[str, Any]], normal: tuple[float, float, float]) -> bool:
    hits = 0
    for frame in frames:
        pose = _pose(frame)
        if pose is None:
            continue
        origin, forward, right, up = _axes(pose)
        delta = _sub(point, origin)
        distance = _norm(delta)
        if not 1.0 <= distance <= 8.0:
            continue
        incidence = _angle(_unit(delta), normal)
        if incidence > 75:
            continue
        if not _in_frame(delta, right, up, forward, frame):
            continue
        hits += 1
    return hits >= 2


def _ground_unseen(frames: list[dict[str, Any]], *, side: int) -> bool:
    pitched = 0
    extent = 0.0
    for frame in frames:
        pose = _pose(frame)
        if pose is None:
            continue
        origin, forward, _right, _up = _axes(pose)
        horizontal = math.hypot(forward[0], forward[2])
        pitch = math.degrees(math.atan2(-forward[1], horizontal or 1e-6))
        along = side * origin[0]
        extent = max(extent, along)
        if pitch >= 20:
            pitched += 1
    return extent > 5 and pitched < 2


def _in_frame(delta, right, up, forward, frame) -> bool:
    intr = frame.get("intrinsics") or [1, 1, 1, 1]
    width = float(frame.get("w") or frame.get("width") or 100)
    height = float(frame.get("h") or frame.get("height") or 100)
    x = _dot(delta, right)
    y = _dot(delta, up)
    z = _dot(delta, forward)
    if z <= 1e-6:
        return False
    u = float(intr[0]) * (x / z) + float(intr[2])
    v = float(intr[3]) - float(intr[1]) * (y / z)
    return 0.03 * width <= u <= 0.97 * width and 0.03 * height <= v <= 0.97 * height


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def _horizontal(a):
    return (a[0], 0.0, a[2])


def _angle(a, b) -> float:
    c = max(-1.0, min(1.0, _dot(_unit(a), _unit(b))))
    return math.degrees(math.acos(c))
