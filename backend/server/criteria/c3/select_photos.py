"""Pick stills for a fact group: purpose first, then frame scores, then object count."""

from __future__ import annotations

PURPOSE = (
    "meter_surround",
    "meter_left",
    "meter_right",
    "adjacent_wall",
    "panel_closed",
    "panel_open",
    "main_disconnect",
)


def select(scene: dict, frame_scores: dict | None, limit: int = 6) -> list[str]:
    stills = scene.get("stills") or []
    ranked = [s["id"] for s in stills if s.get("purpose") in PURPOSE]
    if frame_scores:
        rows = sorted(frame_scores.get("frames", []), key=lambda r: -float(r.get("score", 0)))
        ranked.extend(r["id"] for r in rows if r.get("id") not in ranked)
    keyframes = scene.get("keyframes") or []
    ranked.extend(k["id"] for k in keyframes if k["id"] not in ranked)
    return ranked[:limit]
