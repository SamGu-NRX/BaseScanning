"""Customer copy. Mirrors ios_kit.prompts; never imports ios_kit.

Selection: entries are ordered most specific first. The first entry whose every
present match key contains the given value wins. ``vn.generic`` is last.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import get_args

from server.contracts.capture_check import CaptureCheckCode, HintCode

CAUSE_RANK = (
    "generated_only",
    "unobserved",
    "partial",
    "grazing",
    "unknown_attribute",
    "margin",
)

WHAT = {
    "window": "window",
    "door": "door",
    "garage_door": "garage door",
    "gas_meter": "gas meter",
    "ac_unit": "air-conditioning unit",
    "pool": "pool",
    "panel": "electrical panel",
    "meter": "electric meter",
    "other": "object",
}

# Criterion ids from docs/scene-and-verdict-format.md §5.
CRITERION_IDS = (
    "wall_backing",
    "ground_surface",
    "meter_working_space",
    "gas_clearance",
    "ac_clearance",
    "drive_clearance",
    "pool_clearance",
    "opening_clearance",
    "wall_equipment_above",
    "facing_gap",
    "headroom",
    "route_path",
    "route_length",
    "disconnect_space",
    "walkway",
    "street_facing",
    "requires_member_action",
    "placement",
    "meter_number",
    "cl320",
    "oversized_can",
    "meter_type",
    "meter_mount",
    "multiple_mains",
    "main_panel_type",
    "meter_panel_relationship",
    "meter_not_accessible",
    "panel_not_accessible",
    "panel_in_house",
    "not_same_wall",
    "panel_recall",
)

_REASON_BODY = {
    "drive_clearance": "The spot is closer than {threshold_ft} ft to the driveway.",
    "gas_clearance": "The spot is closer than {threshold_ft} ft to the gas equipment.",
    "ac_clearance": "The spot is closer than {threshold_ft} ft to the air conditioner.",
    "pool_clearance": "The spot is closer than {threshold_ft} ft to the pool.",
    "opening_clearance": "The spot is closer than {threshold_ft} ft to a door or window.",
    "meter_working_space": "The spot overlaps the clear space in front of the meter.",
    "route_length": "The cable route is longer than {threshold_ft} ft.",
    "route_path": "The cable route crosses something it should not.",
    "headroom": "There is not {threshold_ft} ft of clear height above the spot.",
    "facing_gap": "There is not {threshold_ft} ft of open space in front of the wall.",
    "wall_backing": "The battery would not sit fully against a solid wall.",
    "ground_surface": "The ground under the spot is not a surface we can use.",
    "wall_equipment_above": "Something on the wall sits above the spot.",
    "disconnect_space": "There is not enough open wall beside the meter.",
    "walkway": "The spot blocks a walkway.",
    "street_facing": "This wall faces the street.",
    "requires_member_action": "Something in the way has to be moved first.",
    "placement": "No spot on this wall meets the installation rules.",
    "meter_number": "We still need the meter number confirmed.",
    "cl320": "The meter class does not match what this install needs.",
    "oversized_can": "The meter box is larger than this install allows.",
    "meter_type": "This meter type is not one we can use.",
    "meter_mount": "The meter is mounted in a way we cannot use.",
    "multiple_mains": "This home has more than one main disconnect.",
    "main_panel_type": "The main panel is not a type we can use.",
    "meter_panel_relationship": "The meter and the panel are not arranged as required.",
    "meter_not_accessible": "We cannot reach the meter safely.",
    "panel_not_accessible": "We cannot reach the electrical panel safely.",
    "panel_in_house": "The electrical panel is inside the house.",
    "not_same_wall": "The meter and the panel are not on the same wall.",
    "panel_recall": "This electrical panel is on a recall list.",
}

_METER_BODY = (
    "From about 3 ft, photograph your electric meter straight on, then from one step to the side. "
    "Keep the label readable."
)
_PANEL_BODY = (
    "From about 3 ft, photograph the electrical panel with the door closed, then open, "
    "so the labels are readable. Touch nothing inside."
)
_WALL_WALK = "Walk slowly along the wall {side}, 8–12 ft back, keeping the wall in frame."
_GROUND_DOWN = "From chest height, point the phone down at the ground in front of the wall {side}."

# prompt_id, title, body. Shared by a capture-check code and the hint of the same name.
_CODE_COPY: dict[str, tuple[str, str, str]] = {
    "meter_close": ("vn.close_up.meter", "Two close-ups of the meter", _METER_BODY),
    "meter_closeups_in_session": ("vn.close_up.meter", "Two close-ups of the meter", _METER_BODY),
    "meter_oblique": (
        "vn.close_up.meter_oblique",
        "One more meter photo",
        "Take one step to the side and photograph the meter again from about 3 ft, label readable.",
    ),
    "meter_tap": (
        "capture_check.meter_tap",
        "Tap your meter",
        "Point the phone at your electric meter and tap it on the screen once.",
    ),
    "door_view_meter_side": (
        "capture_check.door_view_meter_side",
        "Photograph a door",
        "Photograph any entry or garage door on this side of the house straight on, from about 10 ft back.",
    ),
    "panel_close": ("vn.close_up.panel", "Electrical panel close-up", _PANEL_BODY),
    "panel_open": ("vn.close_up.panel", "Electrical panel close-up", _PANEL_BODY),
    "main_disconnect": ("vn.close_up.panel", "Electrical panel close-up", _PANEL_BODY),
    "keyframe_count": (
        "capture_check.keyframe_count",
        "Keep going",
        "Keep filming while you walk slowly along the wall so we get enough of it.",
    ),
    "keyframe_cadence": (
        "capture_check.keyframe_cadence",
        "Keep going",
        "Keep filming while you walk slowly along the wall so we get enough of it.",
    ),
    "photo_count": (
        "capture_check.photo_count",
        "Keep going",
        "Photograph a few more spots along the wall.",
    ),
    "tracking_quality": (
        "capture_check.tracking_quality",
        "Hold steady",
        "Move a bit more slowly and keep the wall in frame while the phone catches up.",
    ),
    "wall_left_of_meter": ("capture_check.wall_left_of_meter", "Walk the wall", _WALL_WALK),
    "wall_right_of_meter": ("capture_check.wall_right_of_meter", "Walk the wall", _WALL_WALK),
    "ground_downward_left": ("capture_check.ground_downward_left", "Show the ground", _GROUND_DOWN),
    "ground_downward_right": ("capture_check.ground_downward_right", "Show the ground", _GROUND_DOWN),
    "frames_blurry": (
        "hint.frames_blurry",
        "Hold steady",
        "Move a bit more slowly and keep the phone steady.",
    ),
    "frames_dark": ("hint.frames_dark", "Too dark", "Step back so more light reaches the wall."),
    "frames_overexposed": (
        "hint.frames_overexposed",
        "Too bright",
        "Move so the sun is not directly in the picture.",
    ),
    "wall_gap_left": (
        "hint.wall_gap_left",
        "Missing a stretch of wall",
        "Walk back along the wall {side} and film it again from 8–12 ft back.",
    ),
    "wall_gap_right": (
        "hint.wall_gap_right",
        "Missing a stretch of wall",
        "Walk back along the wall {side} and film it again from 8–12 ft back.",
    ),
    "ground_not_seen_left": ("capture_check.ground_downward_left", "Show the ground", _GROUND_DOWN),
    "ground_not_seen_right": ("capture_check.ground_downward_right", "Show the ground", _GROUND_DOWN),
    "meter_plate_unreadable": (
        "hint.meter_plate_unreadable",
        "Step a little closer",
        "Step a little closer so the meter label is sharp.",
    ),
    "meter_number_confirm": (
        "hint.meter_number_confirm",
        "Is this your meter number?",
        "Check the meter number we read and tap to confirm, or pick the right one.",
    ),
    "panel_label_unreadable": (
        "hint.panel_label_unreadable",
        "Panel label not readable",
        "Move closer to the electrical panel so the breaker labels are sharp.",
    ),
}

_VAR = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class PromptEntry:
    prompt_id: str
    match: dict[str, tuple[str, ...]]
    title: str
    body: str


@dataclass(frozen=True)
class RenderedPrompt:
    promptId: str
    title: str
    body: str


@dataclass(frozen=True)
class Catalogue:
    prompts: tuple[PromptEntry, ...]
    source: str


def worst_cause(causes: list[str | None]) -> str | None:
    """Most serious unsure cause. ``generated_only`` outranks ``margin``."""

    best: str | None = None
    best_rank = len(CAUSE_RANK) + 1
    for cause in causes:
        if not cause:
            continue
        rank = CAUSE_RANK.index(cause) if cause in CAUSE_RANK else len(CAUSE_RANK)
        if rank < best_rank:
            best_rank = rank
            best = cause
    return best


def side_text(s_mid: float | None) -> str:
    if s_mid is None:
        return "at your meter"
    if s_mid < -0.3:
        return "to the left of your meter"
    if s_mid > 0.3:
        return "to the right of your meter"
    return "at your meter"


def side_from_code(code: str | None) -> str:
    if code and code.endswith("_left"):
        return "to the left of your meter"
    if code and code.endswith("_right"):
        return "to the right of your meter"
    return "at your meter"


def what_text(target_class: str | None) -> str:
    if not target_class:
        return "object"
    return WHAT.get(target_class, "object")


def _entry(prompt_id: str, title: str, body: str, **match: list[str] | tuple[str, ...]) -> PromptEntry:
    return PromptEntry(
        prompt_id=prompt_id,
        match={key: tuple(values) for key, values in match.items()},
        title=title,
        body=body,
    )


def _fallback_entries() -> tuple[PromptEntry, ...]:
    rows: list[PromptEntry] = [
        _entry(
            "vn.wall.grazing",
            "Face the wall straight on",
            "We only saw this stretch from the side. From 8–12 ft back, {side}, {s_ft} ft along, photograph it head-on.",
            kind=["band"],
            band=["wall"],
            cause=["grazing"],
        ),
        _entry(
            "vn.wall.unobserved",
            "One more wall photo",
            "Stand 8–12 ft back from the wall {side}, {s_ft} ft along, face it squarely and take a photo.",
            kind=["band"],
            band=["wall"],
            cause=["unobserved", "partial", "generated_only"],
        ),
        _entry(
            "vn.wall.unobserved",
            "One more wall photo",
            "Stand 8–12 ft back from the wall {side}, {s_ft} ft along, face it squarely and take a photo.",
            kind=["band"],
            band=["wall"],
        ),
        _entry(
            "vn.ground.unobserved",
            "Show us the ground",
            "From chest height, point the phone down at the ground {side}, {s_ft} ft along, "
            "so we see about {out_ft} ft out from the wall.",
            kind=["band"],
            band=["ground"],
        ),
        _entry(
            "vn.overhead.unobserved",
            "Show what's above",
            "From about 10 ft back, tilt the phone up so the wall {side} is in frame from the ground to the eaves.",
            kind=["band"],
            band=["overhead"],
        ),
        _entry(
            "vn.facing.unobserved",
            "What faces this wall?",
            "Stand with your back to the wall {side}, {s_ft} ft along, and photograph straight out: "
            "fence, hedge, driveway or yard.",
            kind=["band"],
            band=["facing"],
        ),
        _entry(
            "vn.past_end",
            "Where does the wall end?",
            "Walk to the {side} end of this wall and photograph the corner and what comes after it.",
            kind=["past_end"],
        ),
        _entry(
            "vn.close_up.meter",
            "Two close-ups of the meter",
            _METER_BODY,
            kind=["close_up"],
            target_class=["meter"],
        ),
        _entry(
            "vn.close_up.panel",
            "Electrical panel close-up",
            _PANEL_BODY,
            kind=["close_up"],
            target_class=["panel"],
        ),
        _entry(
            "vn.close_up.object",
            "A closer look",
            "Take a closer photo of the {what} {side}, {s_ft} ft along, so we can see it clearly.",
            kind=["close_up"],
        ),
        _entry(
            "vn.confirm.window",
            "Does this window open?",
            "Photograph the window {side}, {s_ft} ft along, close enough to show its frame and latch.",
            kind=["confirm_attribute"],
            target_class=["window"],
        ),
        _entry(
            "vn.confirm.door",
            "A closer look at the door",
            "Photograph the door {side} straight on from about 10 ft back, full frame and threshold in view.",
            kind=["confirm_attribute"],
            target_class=["door", "garage_door"],
        ),
        _entry(
            "vn.confirm.ground",
            "What's on the ground here?",
            "From chest height, photograph the ground {side}, {s_ft} ft along, so we can see what it is made of.",
            kind=["confirm_attribute"],
            target_class=["ground"],
        ),
        _entry(
            "vn.close_up.meter",
            "Two close-ups of the meter",
            _METER_BODY,
            member_action=["retake_meter_closeups"],
        ),
        _entry(
            "ma.truedepth_meter_scan",
            "A quick scan of the meter",
            "Turn the phone around so the screen faces the meter, hold it about a foot away and keep it still for 2 seconds.",
            member_action=["truedepth_meter_scan"],
        ),
        _entry(
            "outcome.eligible",
            "Your home looks eligible",
            "Your home looks eligible. Here's where the battery would go.",
            outcome=["eligible"],
        ),
        _entry(
            "outcome.needs_more_photos",
            "Almost there",
            "One more photo helps us finish your check.",
            outcome=["needs_more_photos"],
        ),
        _entry(
            "outcome.manual_review",
            "We have what we need",
            "We have what we need. Our team will follow up.",
            outcome=["manual_review"],
        ),
        _entry(
            "outcome.not_eligible",
            "Not a fit at this spot",
            "This spot doesn't meet the installation rules.",
            outcome=["not_eligible"],
        ),
        _entry(
            "outcome.failed",
            "Something went wrong",
            "We couldn't process this capture. Please try again, or our team will follow up.",
            outcome=["failed"],
        ),
    ]
    for criterion in CRITERION_IDS:
        rows.append(
            _entry(
                f"reason.{criterion}",
                "Not a fit here",
                _REASON_BODY[criterion],
                criterion=[criterion],
            )
        )
    for code in get_args(CaptureCheckCode):
        prompt_id, title, body = _CODE_COPY[code]
        rows.append(_entry(prompt_id, title, body, capture_check_code=[code]))
    for code in get_args(HintCode):
        prompt_id, title, body = _CODE_COPY[code]
        rows.append(_entry(prompt_id, title, body, hint_code=[code]))
    rows.append(
        _entry(
            "vn.generic",
            "One more photo",
            "Photograph the area {side}, {s_ft} ft along the wall, from about 10 ft back.",
        )
    )
    return tuple(rows)


FALLBACK = Catalogue(prompts=_fallback_entries(), source="fallback")


def catalogue_document(cat: Catalogue) -> dict:
    return {
        "format": "views-needed-prompts",
        "version": "0.4.0",
        "locale": "en-US",
        "limits": {"title_chars": 40, "body_chars": 140},
        "prompts": [
            {
                "prompt_id": entry.prompt_id,
                "match": {k: list(v) for k, v in entry.match.items()},
                "title": entry.title,
                "body": entry.body,
            }
            for entry in cat.prompts
        ],
    }


def load_catalogue(path: Path) -> Catalogue:
    data = json.loads(Path(path).read_text())
    prompts = []
    for raw in data["prompts"]:
        match = {key: tuple(values) for key, values in (raw.get("match") or {}).items()}
        prompts.append(PromptEntry(raw["prompt_id"], match, raw["title"], raw["body"]))
    return Catalogue(tuple(prompts), source=str(path))


def select_prompt(
    cat: Catalogue,
    *,
    kind=None,
    band=None,
    cause=None,
    target_class=None,
    member_action=None,
    hint_code=None,
    capture_check_code=None,
    outcome=None,
    criterion=None,
) -> PromptEntry:
    given = {
        "kind": kind,
        "band": band,
        "cause": cause,
        "target_class": target_class,
        "member_action": member_action,
        "hint_code": hint_code,
        "capture_check_code": capture_check_code,
        "outcome": outcome,
        "criterion": criterion,
    }
    for entry in cat.prompts:
        if _matches(entry, given):
            return entry
    raise KeyError("no prompt matches")


def _matches(entry: PromptEntry, given: dict) -> bool:
    for key, allowed in entry.match.items():
        value = given.get(key)
        if value is None or value not in allowed:
            return False
    return True


def render(entry: PromptEntry, **vars: str | float) -> RenderedPrompt:
    def fill(text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in vars:
                raise KeyError(key)
            return str(vars[key])

        return _VAR.sub(repl, text)

    return RenderedPrompt(promptId=entry.prompt_id, title=fill(entry.title), body=fill(entry.body))
