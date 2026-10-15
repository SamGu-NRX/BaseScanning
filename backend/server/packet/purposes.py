"""Capture-spec still purposes. Unknown values are indexed as other."""

from __future__ import annotations

# Spec §3.4 / capture-packet `stills.purpose`. Thirteen values, including `other`.
KNOWN_PURPOSES = frozenset(
    {
        "meter_close",
        "meter_oblique",
        "meter_surround",
        "meter_left",
        "meter_right",
        "adjacent_wall",
        "behind_fence",
        "panel_close",
        "panel_open",
        "main_disconnect",
        "panel_surround",
        "house_overview",
        "other",
    }
)

# `normalized/stills/{id}.jpg` must stay a single directory entry the uploader globs.
_MAX_STILL_ID = 200


def still_id_issue(value: object) -> str | None:
    """Return why `value` cannot be a still id, or None when it is safe."""
    if not isinstance(value, str) or value == "":
        return "still id must be a non-empty string"
    if "\x00" in value or "/" in value or "\\" in value or value in {".", ".."}:
        return "still id is not a safe file name"
    if len(value) > _MAX_STILL_ID:
        return "still id is too long"
    return None
