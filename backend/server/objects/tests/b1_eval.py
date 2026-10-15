"""Cached presence scorers. Numbers come from the committed B1/B3 answers, not a live model."""

from __future__ import annotations

import json
from pathlib import Path


def siglip_frame_recall(b3: Path) -> dict[str, str]:
    """Port of B3 cascade_eval for siglip / margin 0.5 / unsure+gate, frame level."""
    images = json.loads((b3 / "images.json").read_text())
    gt = json.loads((b3 / "gt.json").read_text())["frames"]
    scores = json.loads((b3 / "gate_scores.json").read_text())["siglip"]
    thresholds = json.loads((b3 / "gate_eval.json").read_text())["siglip"]["margin_0.5"]["thresholds"]
    mini = _jsonl(b3 / "raw/presence/gpt-5.4-mini.jsonl")
    big = _jsonl(b3 / "raw/presence/gpt-5.5.jsonl")
    equip = ["electric_meter", "main_panel", "gas_meter", "ac_condenser", "pool"]
    to_gt = {
        "electric_meter": "meter",
        "main_panel": "panel",
        "gas_meter": "gas_meter",
        "ac_condenser": "ac_condenser",
        "pool": "pool",
    }
    gate_cls = {v: k for k, v in to_gt.items()}

    def fires(fid: str) -> set[str]:
        return {c for c in thresholds if fid in scores and scores[fid][c] >= thresholds[c]}

    def kept(fid: str) -> bool:
        return fid not in scores or bool(fires(fid))

    def escalate(fid: str) -> bool:
        row = mini.get(fid)
        if not row:
            return True
        if any(row["present"].get(c) == "unsure" for c in equip):
            return True
        return any(row["present"].get(gate_cls[c]) == "no" for c in fires(fid))

    def final(fid: str):
        if not kept(fid):
            return None
        if escalate(fid):
            return big.get(fid) or mini.get(fid)
        return mini.get(fid)

    lab = [
        r["id"] for r in images if r.get("set") in {"walk", "commons"} and gt[r["id"]]["labelled"] and r["id"] in big
    ]
    out = {}
    for cls, gate in to_gt.items():
        tp = fn = 0
        for fid in lab:
            ans = final(fid)
            yes = bool(ans) and ans["present"].get(cls) == "yes"
            truth = gate in gt[fid]["classes"]
            tp += yes and truth
            fn += truth and not yes
        out[cls] = f"{tp}/{tp + fn}"
    return out


def pair_presence_recall(b1: Path) -> dict[str, tuple[int, int]]:
    """A class counts as present when GPT-5.5 or Grok 4.7 said yes. Classes with n < 3 are returned too."""
    labels = [json.loads(line) for line in (b1 / "labels.jsonl").read_text().splitlines() if line.strip()]
    gpt = _jsonl(b1 / "vlm/gpt-5.5.jsonl")
    grok = _jsonl(b1 / "vlm/grok-4.7.jsonl")
    # House frames and Commons images that both models answered. Meter close-ups live on the VM.
    ids = [row["id"] for row in labels if row["id"] in gpt and row["id"] in grok and not row["id"].startswith("meter/")]
    classes: dict[str, list[str]] = {}
    by_id = {row["id"]: row for row in labels}
    for fid in ids:
        present = {obj["label"] for obj in by_id[fid].get("objects", [])}
        for cls in present:
            classes.setdefault(cls, []).append(fid)
    recalls = {}
    for cls, frames in classes.items():
        hit = 0
        for fid in frames:
            gpt_yes = gpt[fid].get("present", {}).get(cls) == "yes"
            grok_yes = grok[fid].get("present", {}).get(cls) == "yes"
            hit += gpt_yes or grok_yes
        recalls[cls] = (hit, len(frames))
    return recalls


def _jsonl(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            if "error" not in row:
                out[row["id"]] = row
    return out
