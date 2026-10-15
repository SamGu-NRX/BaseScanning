"""Dev-only meter pixels for fixture replays.

The M2 driver passes ``models['meter_frames']`` and ``models['anchor_label']``
straight into coverage. A live replay cannot. It sends the same pixels on
``POST /v1/captures`` as ``fixtureMeter``, or sets ``HSS_COVERAGE_METER_FRAMES``
and ``HSS_COVERAGE_ANCHOR_FROM_TAP`` on the server process.

Both paths apply only when ``SMP_ENVIRONMENT`` is ``dev``. Production ignores
them, so a missing meter tap still closes on the first attempt (SD-97).
"""

from __future__ import annotations

import os
import re

_PAIR = re.compile(r"([^:,\s]+):([^,]+),([^,\s]+)")
_ANCHOR = re.compile(r"[A-Za-z0-9_-]{1,64}")


class FixtureMeterError(ValueError):
    def __init__(self, pointer: str, message: str) -> None:
        self.pointer = pointer
        super().__init__(message)


def parse_meter_frames(text: str) -> list[tuple[str, float, float]] | None:
    """``id:u,v`` pairs. Commas separate both the pixel and the next frame."""
    if not text:
        return None
    rows = [(fid, float(u), float(v)) for fid, u, v in _PAIR.findall(text)]
    if not rows:
        raise ValueError(f"meter frames must look like k00012:1010,655, not {text!r}")
    return rows


def environment_is_dev(environment: str | None = None) -> bool:
    value = os.environ.get("SMP_ENVIRONMENT") if environment is None else environment
    return value == "dev"


def env_meter_frames() -> str:
    """Process-wide override. Empty unless this process is ``SMP_ENVIRONMENT=dev``."""
    if not environment_is_dev():
        return ""
    return os.environ.get("HSS_COVERAGE_METER_FRAMES", "") or ""


def env_anchor_label() -> str:
    """Process-wide tap label. Empty unless this process is ``SMP_ENVIRONMENT=dev``."""
    if not environment_is_dev():
        return ""
    return os.environ.get("HSS_COVERAGE_ANCHOR_FROM_TAP", "") or ""


def checked_fixture(environment: str, meter_frames: str, anchor_label: str | None) -> dict | None:
    """Validate a capture-level override. Return the stored object only in dev.

    Prod still rejects a malformed value, and otherwise returns None so the
    field is not stored and coverage does not see it.
    """
    try:
        rows = parse_meter_frames(meter_frames)
    except ValueError as exc:
        raise FixtureMeterError("/fixtureMeter/meterFrames", "meter frames must look like k00012:1010,655") from exc
    if not rows:
        raise FixtureMeterError("/fixtureMeter/meterFrames", "meter frames must look like k00012:1010,655")
    anchor = (anchor_label or "").strip() or None
    if anchor is not None and _ANCHOR.fullmatch(anchor) is None:
        raise FixtureMeterError("/fixtureMeter/anchorLabel", "anchor label must be a short token")
    if environment != "dev":
        return None
    return {"meter_frames": meter_frames, "anchor_label": anchor}


def fixture_models_from_extras(extra: dict | None, *, environment: str) -> dict | None:
    """Coverage ``models`` for one capture. None in prod, or when create stored nothing."""
    if environment != "dev" or not isinstance(extra, dict):
        return None
    raw = extra.get("fixture_meter")
    if not isinstance(raw, dict):
        return None
    models: dict[str, str] = {}
    frames = raw.get("meter_frames")
    anchor = raw.get("anchor_label")
    if isinstance(frames, str) and frames:
        models["meter_frames"] = frames
    if isinstance(anchor, str) and anchor:
        models["anchor_label"] = anchor
    return models or None
