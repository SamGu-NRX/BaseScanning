"""Merge and cap the views a verdict still needs. No customer prompt text.

Ranking follows the reconciled brief (A10): more criteria first, then at equal count
head-on shots of a non-meter chain wall, past-end shots, and downward ground shots,
then distance from the meter. ``view_angle_deg`` is omitted until the verdict schema
carries it (SD-57).
"""

from __future__ import annotations

import json
from pathlib import Path

WALL_TARGET = "head-on photo of this wall section from 2-4 m"
GROUND_TARGET = "downward photo of the ground in front of this wall, from chest height"
FACING_TARGET = "photo straight out from this wall"
PAST_TARGET = "walk past the end of this wall and photograph the next stretch"

_KEEP = {"id", "kind", "band", "wall_id", "s_m", "out_m", "side", "target", "criteria", "why"}


def _target(kind: str, band: str | None) -> str:
    if kind == "past_end":
        return PAST_TARGET
    if band == "ground":
        return GROUND_TARGET
    if band == "facing":
        return FACING_TARGET
    if kind == "confirm_attribute":
        return "close photo of this opening showing whether it opens"
    return WALL_TARGET


def coverage_requests(scene_dir: Path) -> list[dict]:
    """WS-06 coverage_report views, with schema-forbidden fields dropped."""
    path = Path(scene_dir) / "coverage" / "coverage_report.json"
    if not path.is_file():
        return []
    doc = json.loads(path.read_text())
    out = []
    for item in doc.get("views_needed") or []:
        row = {k: v for k, v in item.items() if k in _KEEP}
        row.setdefault("kind", "band")
        row.setdefault("criteria", list(item.get("criteria") or []))
        row.setdefault("why", item.get("why") or "unobserved")
        out.append(row)
    return out


def view_requests(path: Path | None) -> list[dict]:
    """WS-07 location_unsure requests from the `objects/view_requests.json` artifact.

    The file is the work-dir key next to `objects/objects_3d.json`. A copy under
    `scene/objects/` is not this artifact.
    """
    if path is None or not Path(path).is_file():
        return []
    doc = json.loads(path.read_text())
    rows = doc if isinstance(doc, list) else doc.get("views_needed") or doc.get("requests") or []
    out = []
    for item in rows:
        row = {k: v for k, v in item.items() if k in _KEEP}
        row.setdefault("kind", "region")
        row.setdefault("criteria", list(item.get("criteria") or ["placement"]))
        row.setdefault("why", "unobserved")
        out.append(row)
    return out


def build(
    items: list[dict],
    max_views: int,
    *,
    meter_wall_id: str | None = None,
    chain_wall_ids: list[str] | None = None,
) -> list[dict]:
    """Merge equal (kind, band, wall_id) whose spans overlap or sit within 0.25 m."""
    chain = set(chain_wall_ids or [])
    merged: list[dict] = []
    for item in items:
        item = {k: v for k, v in dict(item).items() if k in _KEEP}
        if item.get("s_m") is None:
            item.pop("s_m", None)
        item.setdefault("target", _target(item.get("kind", "band"), item.get("band")))
        item.setdefault("criteria", [])
        item.setdefault("why", item.get("why") or "unobserved")
        item.setdefault("kind", "band")
        placed = False
        for have in merged:
            if (have.get("kind"), have.get("band"), have.get("wall_id")) != (
                item.get("kind"),
                item.get("band"),
                item.get("wall_id"),
            ):
                continue
            if _near(have.get("s_m"), item.get("s_m")):
                have["s_m"] = _union(have.get("s_m"), item.get("s_m"))
                have["criteria"] = sorted(set(have["criteria"]) | set(item["criteria"]))
                incoming_why = item.get("why")
                if incoming_why == "grazing" and have.get("why") == "unobserved":
                    have["why"] = "grazing"
                elif incoming_why and incoming_why not in have["why"]:
                    have["why"] = have["why"] + "; " + incoming_why
                placed = True
                break
        if not placed:
            merged.append(item)
    merged.sort(key=lambda v: _rank(v, meter_wall_id, chain))
    kept = merged[:max_views]
    out = []
    fresh = 1
    for view in kept:
        incoming = view.get("id")
        # Reads ids (rd…) are re-numbered. Coverage-report ids stay so WS-10 can
        # correlate them. Everything else is vn1.. in rank order.
        if not incoming or str(incoming).startswith("rd"):
            view["id"] = f"vn{fresh}"
            fresh += 1
        out.append(view)
    return out


def _rank(view: dict, meter_wall_id: str | None, chain: set[str]) -> tuple:
    priority = 1
    if view.get("band") == "ground" or view.get("kind") == "past_end":
        priority = 0
    elif (
        view.get("band") == "wall"
        and view.get("wall_id")
        and view.get("wall_id") != meter_wall_id
        and view.get("wall_id") in chain
    ):
        priority = 0
    return (-len(view.get("criteria") or []), priority, abs(_mid(view.get("s_m"))))


def _mid(span) -> float:
    if not span:
        return 0.0
    return (span[0] + span[1]) / 2


def _near(a, b) -> bool:
    if not a or not b:
        return True
    return min(a[1], b[1]) + 0.25 >= max(a[0], b[0])


def _union(a, b):
    if not a:
        return b
    if not b:
        return a
    return [min(a[0], b[0]), max(a[1], b[1])]
