"""Customer-facing prompt catalogue. WS-10 mirrors this selection; it does not import this module."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOGUE = KIT_ROOT / "prompts" / "views-needed-prompts.en-US.json"

FEET_PER_METRE = 3.2808
SIDE_THRESHOLD_M = 0.3
CAUSE_WORST = (
    "generated_only",
    "unobserved",
    "partial",
    "grazing",
    "unknown_attribute",
    "margin",
)
CLASS_WORDS = {
    "window": "window",
    "door": "door",
    "garage_door": "garage door",
    "gas_meter": "gas meter",
    "ac_unit": "air-conditioning unit",
    "pool": "pool",
    "panel": "electrical panel",
    "other": "object",
}

# Used when server.contracts.capture_check is not importable (SD-20).
CAPTURE_CHECK_CODES = (
    "meter_close",
    "meter_oblique",
    "meter_closeups_in_session",
    "meter_tap",
    "door_view_meter_side",
    "panel_close",
    "panel_open",
    "main_disconnect",
    "keyframe_count",
    "keyframe_cadence",
    "tracking_quality",
    "photo_count",
    "wall_left_of_meter",
    "wall_right_of_meter",
    "ground_downward_left",
    "ground_downward_right",
)
HINT_CODES = CAPTURE_CHECK_CODES + (
    "frames_blurry",
    "frames_dark",
    "frames_overexposed",
    "wall_gap_left",
    "wall_gap_right",
    "ground_not_seen_left",
    "ground_not_seen_right",
    "meter_plate_unreadable",
    "meter_number_confirm",
    "panel_label_unreadable",
)
OUTCOME_HEADLINES = {
    "eligible": ("Your home looks eligible", "Your home looks eligible. Here's where the battery would go."),
    "needs_more_photos": ("Almost there", "One more photo helps us finish your check."),
    "manual_review": ("We have what we need", "We have what we need. Our team will follow up."),
    "not_eligible": ("Not a fit at this spot", "This spot doesn't meet the installation rules."),
    "failed": (
        "Something went wrong",
        "We couldn't process this capture. Please try again, or our team will follow up.",
    ),
}


@dataclass(frozen=True)
class PromptEntry:
    prompt_id: str
    match: dict[str, list[str]]
    title: str
    body: str
    camera: dict | None = None


@dataclass(frozen=True)
class RenderedPrompt:
    promptId: str
    title: str
    body: str


@dataclass(frozen=True)
class Catalogue:
    path: Path
    document: dict
    entries: list[PromptEntry]
    title_chars: int
    body_chars: int


def load_catalogue(path: Path = DEFAULT_CATALOGUE) -> Catalogue:
    document = json.loads(path.read_text())
    limits = document["limits"]
    entries = [
        PromptEntry(
            prompt_id=row["prompt_id"],
            match={key: list(values) for key, values in row.get("match", {}).items()},
            title=row["title"],
            body=row["body"],
            camera=row.get("camera"),
        )
        for row in document["prompts"]
    ]
    return Catalogue(path, document, entries, int(limits["title_chars"]), int(limits["body_chars"]))


def matching_entries(cat: Catalogue, **query: str | None) -> list[PromptEntry]:
    """Every entry whose match keys are satisfied. `select_prompt` returns the first."""
    asked = {
        "kind": query.get("kind"),
        "band": query.get("band"),
        "cause": query.get("cause"),
        "target_class": query.get("target_class"),
        "member_action": query.get("member_action"),
        "hint_code": query.get("hint_code"),
        "capture_check_code": query.get("capture_check_code"),
        "outcome": query.get("outcome"),
        "criterion": query.get("criterion"),
    }
    return [entry for entry in cat.entries if _matches(entry, asked)]


def select_prompt(
    cat: Catalogue,
    *,
    kind: str | None = None,
    band: str | None = None,
    cause: str | None = None,
    target_class: str | None = None,
    member_action: str | None = None,
    hint_code: str | None = None,
    capture_check_code: str | None = None,
    outcome: str | None = None,
    criterion: str | None = None,
) -> PromptEntry:
    """First entry whose every match key contains the given value. `vn.generic` is last."""
    found = matching_entries(
        cat,
        kind=kind,
        band=band,
        cause=cause,
        target_class=target_class,
        member_action=member_action,
        hint_code=hint_code,
        capture_check_code=capture_check_code,
        outcome=outcome,
        criterion=criterion,
    )
    if not found:
        raise KeyError("catalogue has no matching prompt")
    return found[0]


def _matches(entry: PromptEntry, query: dict[str, str | None]) -> bool:
    if not entry.match:
        return True
    for key, allowed in entry.match.items():
        value = query.get(key)
        if value is None or value not in allowed:
            return False
    return True


def side_phrase(s_m: float) -> str:
    if s_m < -SIDE_THRESHOLD_M:
        return "to the left of your meter"
    if s_m > SIDE_THRESHOLD_M:
        return "to the right of your meter"
    return "at your meter"


def class_phrase(target_class: str | None) -> str:
    if target_class is None:
        return CLASS_WORDS["other"]
    return CLASS_WORDS.get(target_class, CLASS_WORDS["other"])


def feet_text(metres: float) -> str:
    return str(int(round(abs(metres) * FEET_PER_METRE)))


def render(entry: PromptEntry, **vars: str | float) -> RenderedPrompt:
    """Substitute `{name}`. Float `s_ft` / `out_ft` are metres and become whole feet."""
    values: dict[str, str] = {}
    for key, value in vars.items():
        if key in ("s_ft", "out_ft") and isinstance(value, float | int) and not isinstance(value, bool):
            values[key] = feet_text(float(value))
        else:
            values[key] = str(value)
    try:
        title = entry.title.format(**values)
        body = entry.body.format(**values)
    except KeyError as exc:
        raise KeyError(f"missing prompt var {exc}") from exc
    if values.get("s_ft") == "0":
        title = _drop_zero_offset(title)
        body = _drop_zero_offset(body)
    if "{" in title or "{" in body:
        raise KeyError("unresolved prompt placeholder")
    return RenderedPrompt(entry.prompt_id, title, body)


def _drop_zero_offset(text: str) -> str:
    """A view centred on the meter is 'at your meter', not '0 ft along'."""
    return text.replace(", 0 ft along the wall", "").replace(", 0 ft along", "")


def worst_cause(causes: list[str]) -> str | None:
    for cause in CAUSE_WORST:
        if cause in causes:
            return cause
    return causes[0] if causes else None
