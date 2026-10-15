"""Photo-flow meter candidates for WS-04 when the packet has no meter tap."""

from __future__ import annotations


def meter_candidates(
    packet: dict, *, top_k: int = 3, detections: dict | None = None, clip_scores: dict | None = None
) -> list[dict]:
    """OWLv2 'electric meter' at score >= 0.3, ranked by the CLIP meter score.

    `detections` is frame id -> list of {label, score, box}. `clip_scores` is frame -> {meter: score}.
    """
    detections = detections or {}
    clip_scores = clip_scores or {}
    taps = packet.get("taps") or []
    if any((tap.get("label") == "meter") for tap in taps):
        return []
    rows = []
    for fid, dets in detections.items():
        purpose = _purpose(packet, fid)
        if purpose and not str(purpose).startswith("meter_") and _kind(packet, fid) == "still":
            continue
        for det in dets:
            label = det["label"] if isinstance(det, dict) else det.label
            score = det["score"] if isinstance(det, dict) else det.score
            if label != "electric_meter" or score < 0.3:
                continue
            rows.append(
                {
                    "frame": fid,
                    "score": score,
                    "clip_meter": float((clip_scores.get(fid) or {}).get("meter", 0.0)),
                    "box": det["box"] if isinstance(det, dict) else det.box,
                }
            )
    rows.sort(key=lambda r: (-r["clip_meter"], -r["score"]))
    return rows[:top_k]


def _purpose(packet: dict, fid: str) -> str | None:
    for still in packet.get("stills") or []:
        if still.get("id") == fid:
            return still.get("purpose")
    return None


def _kind(packet: dict, fid: str) -> str:
    for still in packet.get("stills") or []:
        if still.get("id") == fid:
            return "still"
    return "keyframe"
