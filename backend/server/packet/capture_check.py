"""The "you can leave" checklist."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from server.contracts.capture_check import CaptureCheckCode
from server.packet.params import DEFAULT_PARAMS, PacketParams

POSE_CODES = (
    "wall_left_of_meter",
    "wall_right_of_meter",
    "ground_downward_left",
    "ground_downward_right",
)

DEFAULT_MESSAGES = {
    "meter_close": "Take a close photo of the meter, straight on, about 3 feet away.",
    "meter_oblique": "Take a second meter photo from about 30 degrees to one side.",
    "meter_closeups_in_session": "Retake both meter photos inside the walk, not from the camera roll.",
    "meter_tap": "Tap the meter on the screen during the walk.",
    "door_view_meter_side": "Show the door on the meter's wall.",
    "panel_close": "Photograph the electrical panel closed.",
    "panel_open": "Photograph the electrical panel with the door open.",
    "main_disconnect": "Photograph the main disconnect.",
    "keyframe_count": "Walk the wall a little longer so we have more views.",
    "keyframe_cadence": "Walk more slowly and keep the phone pointing at the wall.",
    "tracking_quality": "Move more slowly; the phone lost track of the wall.",
    "photo_count": "Add photos until you have about 40, one per step along each wall.",
    "wall_left_of_meter": "Photograph the wall to the left of the meter.",
    "wall_right_of_meter": "Photograph the wall to the right of the meter.",
    "ground_downward_left": "Point the phone down to show the ground left of the meter.",
    "ground_downward_right": "Point the phone down to show the ground right of the meter.",
}


class CheckItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: CaptureCheckCode
    status: Literal["present", "missing", "weak", "unknown", "not_applicable"]
    severity: Literal["required", "recommended"]
    retake: str | None = None
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class CaptureCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["hss-capture-check/v0"] = "hss-capture-check/v0"
    mode: Literal["partial", "final"]
    complete: bool
    pendingFinal: bool
    missing: list[CaptureCheckCode]
    unknown: list[CaptureCheckCode]
    items: list[CheckItem]
    stats: dict[str, float]


class PartialPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session: dict[str, Any]
    keyframes: list[dict[str, Any]]
    stills: list[dict[str, Any]]


def partial_from_metas(session: dict, metas: Iterable[dict]) -> PartialPacket:
    keyframes: list[dict] = []
    stills: list[dict] = []
    for meta in metas:
        if "keyframe" in meta:
            keyframes.append(meta["keyframe"])
        elif "still" in meta:
            stills.append(meta["still"])
    return PartialPacket(session=dict(session), keyframes=keyframes, stills=stills)


def _packet(pk) -> dict:
    if isinstance(pk, PartialPacket):
        return {"session": pk.session, "keyframes": pk.keyframes, "stills": pk.stills}
    if isinstance(pk, dict):
        return pk
    raw = getattr(pk, "_packet", None)
    if isinstance(raw, dict):
        return raw
    if hasattr(pk, "model_dump"):
        return pk.model_dump()
    raise TypeError(type(pk))


def _purpose_ids(pk: dict, purpose: str) -> list[str]:
    found = []
    for frame in pk.get("keyframes") or []:
        if frame.get("purpose") == purpose:
            found.append(frame.get("id"))
    for still in pk.get("stills") or []:
        if still.get("purpose") == purpose:
            found.append(still.get("id"))
    return [i for i in found if i]


def _tier(pk: dict) -> str:
    return str((pk.get("session") or {}).get("tier") or "")


def _arkit(pk: dict) -> bool:
    return _tier(pk) in {"arkit", "arkit_lidar"}


def _flow(pk: dict) -> str:
    derived = (pk.get("ext") or {}).get("hss") or {}
    if isinstance(derived, dict) and derived.get("flow") in {"guided", "photo"}:
        return derived["flow"]
    if hasattr(pk, "derived"):
        return pk.derived.flow
    return "photo" if _tier(pk) == "photo_only" else "guided"


def _report_ok(pk: dict) -> bool:
    derived = (pk.get("ext") or {}).get("hss") or {}
    validation = derived.get("validation") if isinstance(derived, dict) else None
    if isinstance(validation, dict) and "ok" in validation:
        return bool(validation["ok"])
    return True


def _item(code: str, status: str, severity: str, evidence: dict | None = None) -> CheckItem:
    retake = code if status in {"missing", "weak"} else None
    return CheckItem(
        code=cast(CaptureCheckCode, code),
        status=status,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        retake=retake,
        message=DEFAULT_MESSAGES.get(code, code),
        evidence=evidence or {},
    )


def _close_up(pk: dict, mode: str, which: str, purpose: str) -> str:
    if mode == "partial":
        return "present" if _purpose_ids(pk, purpose) else "missing"
    sr = pk.get("scaleReference") or {}
    if which == "meterCloseUp":
        block = sr.get("meterCloseUp") or {}
    else:
        block = ((sr.get("ext") or {}).get("obliqueCloseUp")) or {}
    stills = {s.get("id"): s for s in pk.get("stills") or []}
    captured = bool(block.get("captured"))
    still = stills.get(block.get("still"))
    if captured and still is not None:
        return "present"
    if _purpose_ids(pk, purpose) and captured and still is not None:
        return "present"
    return "missing"


def capture_check(
    pk,
    *,
    mode: Literal["partial", "final"],
    coverage_missing: Iterable[str] = (),
    params: PacketParams = DEFAULT_PARAMS,
    report_ok: bool | None = None,
) -> CaptureCheck:
    data = _packet(pk)
    missing_cov = set(coverage_missing)
    arkit = _arkit(data)
    flow = pk.derived.flow if hasattr(pk, "derived") else _flow(data)
    guided = flow == "guided"
    photo = flow == "photo" or _tier(data) == "photo_only"
    items: list[CheckItem] = []
    items.append(
        _item(
            "meter_close",
            _close_up(data, mode, "meterCloseUp", "meter_close"),
            "required",
            {"ids": _purpose_ids(data, "meter_close")},
        )
    )
    items.append(
        _item(
            "meter_oblique",
            _close_up(data, mode, "obliqueCloseUp", "meter_oblique"),
            "required",
            {"ids": _purpose_ids(data, "meter_oblique")},
        )
    )
    if arkit:
        both = _linked(data, "meter_close") and _linked(data, "meter_oblique")
        items.append(_item("meter_closeups_in_session", "present" if both else "missing", "recommended"))
    else:
        items.append(_item("meter_closeups_in_session", "not_applicable", "recommended"))
    if not guided:
        items.append(_item("meter_tap", "not_applicable", "required"))
    elif mode == "partial":
        items.append(_item("meter_tap", "unknown", "required"))
    else:
        tapped = any(t.get("label") == "meter" for t in data.get("taps") or [])
        items.append(_item("meter_tap", "present" if tapped else "missing", "required"))
    door_flag = ((data.get("scaleReference") or {}).get("ext") or {}).get("doorOnMeterSide")
    if not arkit or door_flag is False:
        items.append(_item("door_view_meter_side", "not_applicable", "required"))
    elif mode == "partial":
        items.append(_item("door_view_meter_side", "unknown", "required"))
    else:
        views = ((data.get("scaleReference") or {}).get("ext") or {}).get("doorViews") or []
        items.append(
            _item(
                "door_view_meter_side",
                "present" if views else "missing",
                "required",
                {"frames": [v.get("frame") for v in views]},
            )
        )
    for code in ("panel_close", "panel_open", "main_disconnect"):
        ids = _purpose_ids(data, code)
        items.append(_item(code, "present" if ids else "missing", "recommended", {"ids": ids}))
    n_kf = len(data.get("keyframes") or [])
    if arkit:
        items.append(
            _item(
                "keyframe_count",
                "present" if n_kf >= params.keyframe_count_min else "weak",
                "recommended",
                {"keyframes": n_kf},
            )
        )
        spacing = _spacing(data)
        median_spacing = spacing["medianSpacingM"]
        per_second = spacing["keyframesPerS"]
        cadence_ok = (
            median_spacing is not None
            and per_second is not None
            and median_spacing <= params.median_spacing_m
            and per_second >= params.keyframes_per_s_min
        )
        items.append(_item("keyframe_cadence", "present" if cadence_ok else "weak", "recommended", spacing))
    else:
        items.append(_item("keyframe_count", "not_applicable", "recommended"))
        items.append(_item("keyframe_cadence", "not_applicable", "recommended"))
    limited = _limited_share(data)
    blurred = _flag_share(data, "motion_blur")
    tracking_ok = limited <= params.limited_tracking_share and blurred <= params.blurred_share_max
    items.append(
        _item(
            "tracking_quality",
            "present" if tracking_ok else "weak",
            "recommended",
            {"limitedShare": limited, "blurredShare": blurred},
        )
    )
    n_stills = len(data.get("stills") or [])
    purposes = sorted({still.get("purpose") for still in data.get("stills") or [] if still.get("purpose")})
    if photo and not guided:
        items.append(
            _item(
                "photo_count",
                "present" if n_stills >= params.photo_count_min else "weak",
                "recommended",
                {"stills": n_stills, "purposes": purposes},
            )
        )
    elif _tier(data) == "photo_only":
        items.append(
            _item(
                "photo_count",
                "present" if n_stills >= params.photo_count_min else "weak",
                "recommended",
                {"stills": n_stills, "purposes": purposes},
            )
        )
    else:
        items.append(_item("photo_count", "not_applicable", "recommended"))
    for code in POSE_CODES:
        if code in missing_cov:
            items.append(_item(code, "missing", "required"))
        elif arkit:
            items.append(_item(code, "unknown", "required"))
        else:
            items.append(_item(code, "not_applicable", "required"))
    ok = _report_ok(data) if report_ok is None else report_ok
    required_missing = [item.code for item in items if item.severity == "required" and item.status == "missing"]
    unknown = [item.code for item in items if item.status == "unknown"]
    complete = not required_missing and (ok if mode == "final" else True)
    spacing = _spacing(data)
    median_spacing = spacing["medianSpacingM"]
    per_second = spacing["keyframesPerS"]
    stats = {
        "keyframes": float(n_kf),
        "stills": float(n_stills),
        "limitedShare": float(limited),
        "blurredShare": float(blurred),
        "medianSpacingM": float(0.0 if median_spacing is None else median_spacing),
        "keyframesPerS": float(0.0 if per_second is None else per_second),
    }
    return CaptureCheck(
        mode=mode,
        complete=complete,
        pendingFinal=any(item.severity == "required" and item.status == "unknown" for item in items),
        missing=required_missing,
        unknown=unknown,
        items=items,
        stats=stats,
    )


def _linked(pk: dict, purpose: str) -> bool:
    stills = [s for s in pk.get("stills") or [] if s.get("purpose") == purpose]
    if not stills:
        return False
    return all(bool(s.get("keyframe")) for s in stills)


def _flag_share(pk: dict, flag: str) -> float:
    frames = pk.get("keyframes") or []
    if not frames:
        return 0.0
    hit = 0
    for frame in frames:
        flags = ((frame.get("ext") or {}).get("hss") or {}).get("flags") or []
        if flag in flags:
            hit += 1
    return hit / len(frames)


def _limited_share(pk: dict) -> float:
    frames = pk.get("keyframes") or []
    if not frames:
        return 0.0
    limited = sum(1 for frame in frames if frame.get("tracking") not in (None, "normal"))
    return limited / len(frames)


def _spacing(pk: dict) -> dict[str, float | None]:
    from server.packet.normalize import epoch_motion

    epochs = epoch_motion(pk)
    primary = next((ep for ep in epochs if ep.get("primary")), None)
    if primary is None and epochs:
        primary = epochs[0]
    if primary is None:
        return {"medianSpacingM": None, "keyframesPerS": 0.0}
    return {"medianSpacingM": primary.get("medianSpacingM"), "keyframesPerS": primary.get("keyframesPerS", 0.0)}


def hint_payloads(check: CaptureCheck) -> list[dict[str, str]]:
    """HintData bodies for missing and weak items. WS-03 emits them."""
    hints = []
    for item in check.items:
        if item.status in {"missing", "weak"}:
            hints.append({"code": item.code, "message": item.message, "retake": item.retake or item.code})
    return hints
