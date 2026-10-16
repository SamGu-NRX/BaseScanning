"""Per-house top-K frame selection. Port of B3 select_eval.select."""

from __future__ import annotations

from server.objects.config import FrameSelection
from server.objects.prompts import ALWAYS_PURPOSES, GATE_EQUIP, GATE_HAZARD, WS08_PURPOSES


def select_house(
    frames: list[str],
    score: dict[str, dict[str, float]],
    *,
    k: int,
    min_gap: int = 2,
) -> tuple[list[str], list[str]]:
    """Top k per equipment class, skipping a frame within min_gap positions of one already picked."""
    chosen: set[str] = set()
    hazard: set[str] = set()
    # A missing or unreadable frame is absent from `score`. Skip it instead of raising.
    present = [j for j in range(len(frames)) if isinstance(score.get(frames[j]), dict)]
    for cls in GATE_EQUIP:
        order = sorted(present, key=lambda j: -float(score[frames[j]].get(cls, 0.0)))
        picked: list[int] = []
        for j in order:
            if len(picked) >= k:
                break
            if all(abs(j - q) > min_gap for q in picked):
                picked.append(j)
        ids = [frames[j] for j in picked]
        chosen.update(ids)
        if cls in GATE_HAZARD:
            hazard.update(ids)
    # Stable capture order.
    ordered = [f for f in frames if f in chosen]
    hazards = [f for f in frames if f in hazard]
    return ordered, hazards


def select_frames(
    scores: dict[str, dict[str, dict[str, float]]],
    order: list[str],
    *,
    k: int,
    min_gap: int = 2,
    always: list[str] | None = None,
    houses: dict[str, str] | None = None,
    purposes: dict[str, str] | None = None,
    max_selected: int = 20,
) -> FrameSelection:
    """Union of every gate's top-k, plus checklist stills, capped at max_selected per house.

    `scores` is model -> frame -> class -> score. `order` is capture order.
    Houses with at most max_selected frames are kept whole. Gas-meter and pool picks are still
    recorded as hazard frames on those houses.
    """
    always = list(always or [])
    purposes = purposes or {}
    if houses is None:
        houses = {fid: fid.split("/")[0] for fid in order}
    by_house: dict[str, list[str]] = {}
    for fid in order:
        by_house.setdefault(houses.get(fid, fid), []).append(fid)

    models = [m for m in scores if m in {"clip", "siglip"}] or list(scores)
    selected: dict[str, list[str]] = {}
    hazard: list[str] = []
    for house, frames in by_house.items():
        if len(frames) <= max_selected:
            haz: set[str] = set()
            if _scores_cover(frames, scores, models):
                for model in models:
                    _got, hz = select_house(frames, scores[model], k=k, min_gap=min_gap)
                    haz.update(hz)
            selected[house] = list(frames)
            hazard.extend(f for f in frames if f in haz)
            continue
        union: set[str] = set()
        haz: set[str] = set()
        for model in models:
            got, hz = select_house(frames, scores[model], k=k, min_gap=min_gap)
            union.update(got)
            haz.update(hz)
        for fid in always:
            if houses.get(fid) == house and purposes.get(fid) not in WS08_PURPOSES:
                union.add(fid)
        ordered = [f for f in frames if f in union]
        if len(ordered) > max_selected:
            ranked = sorted(ordered, key=lambda fid: _rank(fid, scores, models), reverse=True)
            keep_ids = set()
            for fid in ranked:
                if fid in haz or len(keep_ids) < max_selected:
                    if fid in haz or len([i for i in keep_ids if i not in haz]) < max_selected:
                        keep_ids.add(fid)
            # Drop lowest non-hazard extras until the cap holds, always keeping hazards.
            non = [f for f in ranked if f not in haz]
            keep_non = non[: max(0, max_selected - len(haz))]
            keep_ids = set(keep_non) | haz
            ordered = [f for f in frames if f in keep_ids]
        selected[house] = ordered
        hazard.extend(f for f in frames if f in haz)

    frames_out = [fid for frames in selected.values() for fid in frames]
    # Stills with an always-purpose that were not in `order` (no gate score) still join S.
    known = set(frames_out)
    for fid in always:
        if fid not in known and purposes.get(fid) not in WS08_PURPOSES:
            frames_out.append(fid)
    n = len(order) or 1
    return FrameSelection(
        frames=frames_out,
        hazard_frames=[f for f in order if f in set(hazard)],
        filtered_frac=1.0 - len([f for f in frames_out if f in set(order)]) / n,
        by_house=selected,
    )


def _scores_cover(frames: list[str], scores: dict, models: list[str]) -> bool:
    """Hazard ranking needs a score for every equipment class. Incomplete rows are not ranked."""
    for model in models:
        table = scores.get(model) or {}
        for fid in frames:
            row = table.get(fid)
            if not isinstance(row, dict) or any(cls not in row for cls in GATE_EQUIP):
                return False
    return True


def _rank(fid: str, scores: dict[str, dict[str, dict[str, float]]], models: list[str]) -> float:
    best = 0.0
    for model in models:
        row = scores.get(model, {}).get(fid) or {}
        if row:
            best = max(best, max(row.values()))
    return best


def eligible_for_vlm(purpose: str | None) -> bool:
    return purpose not in WS08_PURPOSES


def always_still(purpose: str | None) -> bool:
    return purpose in ALWAYS_PURPOSES
