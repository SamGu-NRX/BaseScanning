"""Cheap presence, GPT-5.5 confirmation, Grok second read, and the budget."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from server.objects.config import PLANNED_USD, ObjectsConfig
from server.objects.gate import frame_needs_transpose
from server.objects.prompts import DECISION
from server.objects.proposals import iou
from server.objects.vlm import ProviderDown, VisionClient

Answer = str  # yes | no | unsure | unavailable


def agreement(gpt: Answer, grok: Answer) -> tuple[str, float]:
    """Return (state, confidence). both-no drops the candidate (state 'drop')."""
    if gpt == "yes" and grok == "yes":
        return "agree", 0.9
    if gpt == "yes" and grok in {"no", "unsure", "unavailable"}:
        return "disagree", 0.5
    if gpt == "unsure" and grok == "yes":
        return "disagree", 0.5
    if gpt == "no" and grok == "no":
        return "drop", 0.0
    if gpt == "unavailable" and grok == "yes":
        return "single", 0.5
    if gpt == "unavailable" and grok == "no":
        return "drop", 0.0
    if gpt == "yes" and grok == "off":
        return "single", 0.75
    if gpt == "unsure":
        return "unsure", 0.5
    if gpt == "no":
        return "drop", 0.0
    return "unsure", 0.5


@dataclass
class Budget:
    cap: float
    spent: float = 0.0
    skipped_stages: list[str] = field(default_factory=list)
    calls: dict[str, int] = field(default_factory=dict)
    cost_usd: dict[str, float] = field(default_factory=dict)

    def allow(self, stage: str, planned: float) -> bool:
        """Hazard confirms are never skipped. Later stages drop once the cap would be met."""
        if stage == "hazard":
            return True
        if self.spent + planned >= self.cap:
            if stage not in self.skipped_stages:
                self.skipped_stages.append(stage)
            return False
        return True

    def charge(self, model: str, amount: float) -> None:
        self.spent += amount
        self.calls[model] = self.calls.get(model, 0) + 1
        self.cost_usd[model] = self.cost_usd.get(model, 0.0) + amount


@dataclass
class Candidate:
    cls: str
    frames: list[str]
    boxes: dict[str, list[float]]  # frame -> normalised box
    scores: dict[str, float]
    confidence: float = 0.3
    state: str = "detector"
    grok_box: list[float] | None = None
    best_frame: str = ""
    # Every seed-threshold box, not only the highest-scoring one per frame.
    all_boxes: list[tuple[str, list[float], float]] = field(default_factory=list)


@dataclass
class CascadeResult:
    presence: dict
    candidates: list[Candidate]
    budget: Budget
    warnings: list[str] = field(default_factory=list)


def stored_gate_thresholds(model: str = "siglip") -> dict[str, float]:
    """Cross-fitted thresholds from the committed gate eval. SigLIP uses margin 0.5 (cascade default)."""
    path = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "objects" / "b3" / "gate_eval.json"
    if not path.is_file():
        return {}
    doc = json.loads(path.read_text())
    margin = "margin_0.5" if model == "siglip" else "margin_0.8"
    raw = ((doc.get(model) or {}).get(margin) or {}).get("thresholds") or {}
    return {str(key): float(value) for key, value in raw.items()}


def run_cascade(
    sel,
    frames_by_id: dict,
    client: VisionClient | None,
    budget: Budget,
    cfg: ObjectsConfig,
    detections: dict,
    *,
    gate_scores: dict | None = None,
    gate_thresholds: dict | None = None,
) -> CascadeResult:
    """Steps 6–9. `detections` is frame -> list of Detection (or dicts)."""
    warnings: list[str] = []
    presence: dict[str, dict] = {}
    gate_scores = gate_scores or {}
    if gate_thresholds is None:
        gate_thresholds = stored_gate_thresholds("siglip" if "siglip" in gate_scores or not gate_scores else "clip")
    if not cfg.external_vision or client is None:
        warnings.append("external_vision_disabled")
        budget.skipped_stages.extend(["cheap", "confirm", "second"])
        return CascadeResult(
            presence={}, candidates=_detector_candidates(sel, detections, cfg), budget=budget, warnings=warnings
        )

    # (a) GPT-5.5 on hazard frames before any cheaper call. Hazard is never skipped.
    confirm_answers: dict[str, dict] = {}
    openai_down = False
    for fid in sel.hazard_frames:
        if openai_down:
            break
        frame = frames_by_id.get(fid)
        if frame is None:
            continue
        budget.allow("hazard", PLANNED_USD["gpt-5.5"])
        ans, stopped = _presence(client, "gpt-5.5", frame, warnings)
        openai_down = openai_down or stopped
        if ans is None:
            continue
        budget.charge("gpt-5.5", ans.cost_usd if not ans.cached else 0.0)
        confirm_answers[fid] = {"present": ans.present, "ground_type": ans.ground_type, "quality": ans.quality}
        presence.setdefault(fid, {})["confirm"] = confirm_answers[fid]

    # (b) mini on every selected frame. A miss here also drops later stages.
    mini_answers: dict[str, dict] = {}
    for fid in sel.frames:
        if openai_down:
            break
        frame = frames_by_id.get(fid)
        if frame is None:
            continue
        if not budget.allow("cheap", PLANNED_USD["gpt-5.4-mini"]):
            _exhaust(budget, warnings, ("cheap", "confirm", "second"))
            break
        ans, stopped = _presence(client, "gpt-5.4-mini", frame, warnings)
        openai_down = openai_down or stopped
        if ans is None:
            continue
        budget.charge("gpt-5.4-mini", ans.cost_usd if not ans.cached else 0.0)
        mini_answers[fid] = {"present": ans.present, "ground_type": ans.ground_type, "quality": ans.quality}
        presence.setdefault(fid, {})["cheap"] = mini_answers[fid]

    # (c) GPT-5.5 on the remaining frames that still need a confirm.
    if "confirm" not in budget.skipped_stages and not openai_down:
        for fid in sel.frames:
            if openai_down:
                break
            if fid in confirm_answers:
                continue
            need = _needs_confirm(fid, mini_answers.get(fid), sel, gate_scores, gate_thresholds)
            if not need:
                continue
            if not budget.allow("confirm", PLANNED_USD["gpt-5.5"]):
                _exhaust(budget, warnings, ("confirm", "second"))
                break
            frame = frames_by_id.get(fid)
            if frame is None:
                continue
            ans, stopped = _presence(client, "gpt-5.5", frame, warnings)
            openai_down = openai_down or stopped
            if ans is None:
                continue
            budget.charge("gpt-5.5", ans.cost_usd if not ans.cached else 0.0)
            confirm_answers[fid] = {"present": ans.present, "ground_type": ans.ground_type, "quality": ans.quality}
            presence.setdefault(fid, {})["confirm"] = confirm_answers[fid]

    candidates = _candidates_from_answers(sel, mini_answers, confirm_answers, detections, cfg)
    if cfg.second_reader == "off":
        for cand in candidates:
            if cand.state == "yes":
                cand.confidence = 0.75
        if "second" not in budget.skipped_stages:
            budget.skipped_stages.append("second")
    elif "second" in budget.skipped_stages:
        for cand in candidates:
            cand.confidence = min(cand.confidence, 0.5)
    else:
        grok_answers: dict = {}
        if openai_down:
            grok_answers = _ask_grok_frames(sel, frames_by_id, client, budget, warnings, presence)
            _adopt_grok_yeses(candidates, grok_answers)
        decision = [c for c in candidates if c.cls in DECISION and c.best_frame not in grok_answers]
        for cand in decision[:8]:
            if not budget.allow("second", PLANNED_USD["grok-4.7"]):
                _exhaust(budget, warnings, ("second",))
                for rest in decision[decision.index(cand) :]:
                    rest.confidence = min(rest.confidence, 0.5)
                break
            frame = frames_by_id.get(cand.best_frame)
            if frame is None:
                continue
            try:
                ans = client.boxes("grok-4.7", frame.image, transpose=frame_needs_transpose(frame))
            except ProviderDown:
                warnings.append("vlm_unavailable")
                break
            except RuntimeError:
                state, conf = agreement(_gpt_of(cand, confirm_answers, mini_answers), "unavailable")
                cand.state, cand.confidence = state, conf
                warnings.append("vlm_unavailable")
                continue
            budget.charge("grok-4.7", ans.cost_usd if not ans.cached else 0.0)
            grok = ans.present.get(cand.cls, "unsure")
            gpt = _gpt_of(cand, confirm_answers, mini_answers)
            state, conf = agreement(gpt, grok)
            cand.state, cand.confidence = state, conf
            presence.setdefault(cand.best_frame, {})["second"] = {
                "present": ans.present,
                "ground_type": ans.ground_type,
            }
            seed = cand.boxes.get(cand.best_frame)
            grok_box = next((o["box"] for o in ans.objects if o["label"] == cand.cls), None)
            if grok_box and not seed and cand.best_frame:
                # A confirmed class with no proposer box keeps the second reader's box.
                cand.boxes[cand.best_frame] = grok_box
                cand.all_boxes.append((cand.best_frame, grok_box, cand.confidence))
            elif seed and grok_box and iou(seed, grok_box) >= 0.3:
                cand.grok_box = grok_box
            elif grok_box:
                presence.setdefault(cand.best_frame, {}).setdefault("second_boxes_rejected", []).append(
                    {"label": cand.cls, "box": grok_box}
                )
        candidates = [c for c in candidates if c.state != "drop"]
    return CascadeResult(presence=presence, candidates=candidates, budget=budget, warnings=warnings)


def _presence(client, model: str, frame, warnings: list[str]):
    """One presence call. An OpenAI quota error returns stopped=True and does not try the next GPT frame."""
    try:
        ans = client.presence(model, frame.image, transpose=frame_needs_transpose(frame))
    except ProviderDown as exc:
        warnings.append("vlm_unavailable")
        return None, exc.provider == "openai"
    except RuntimeError:
        warnings.append("vlm_unavailable")
        return None, False
    return ans, False


def _ask_grok_frames(sel, frames_by_id, client, budget: Budget, warnings: list[str], presence: dict) -> dict:
    """Grok reads hazard frames first, then the rest of the selection. Cap is 8 calls."""
    order: list[str] = []
    for fid in [*sel.hazard_frames, *sel.frames]:
        if fid not in order:
            order.append(fid)
    answers: dict = {}
    calls = 0
    for fid in order:
        if calls >= 8:
            break
        if "second" in budget.skipped_stages:
            break
        if not budget.allow("second", PLANNED_USD["grok-4.7"]):
            _exhaust(budget, warnings, ("second",))
            break
        frame = frames_by_id.get(fid)
        if frame is None:
            continue
        calls += 1
        try:
            ans = client.boxes("grok-4.7", frame.image, transpose=frame_needs_transpose(frame))
        except ProviderDown:
            warnings.append("vlm_unavailable")
            break
        except RuntimeError:
            warnings.append("vlm_unavailable")
            continue
        budget.charge("grok-4.7", ans.cost_usd if not ans.cached else 0.0)
        answers[fid] = ans
        presence.setdefault(fid, {})["second"] = {"present": ans.present, "ground_type": ans.ground_type}
    return answers


def _adopt_grok_yeses(candidates: list[Candidate], answers: dict) -> None:
    """Keep a decision class Grok marked yes when GPT never answered it."""
    have = {cand.cls for cand in candidates}
    for fid, ans in answers.items():
        for cls, grok in (ans.present or {}).items():
            if cls in have or cls not in DECISION or grok != "yes":
                continue
            box = next((obj["box"] for obj in ans.objects if obj.get("label") == cls), None)
            state, conf = agreement("unavailable", "yes")
            boxes = {fid: box} if box else {}
            scores = {fid: float(conf)} if box else {}
            kept = [(fid, box, float(conf))] if box else []
            cand = Candidate(
                cls,
                [fid],
                boxes,
                scores,
                confidence=conf,
                state=state,
                best_frame=fid,
                all_boxes=kept,
                grok_box=box,
            )
            candidates.append(cand)
            have.add(cls)


def _exhaust(budget: Budget, warnings: list[str], stages: tuple[str, ...]) -> None:
    """Record each remaining stage once. Hazard is never in this list."""
    for stage in stages:
        if stage not in budget.skipped_stages:
            budget.skipped_stages.append(stage)
        tag = f"budget_exhausted:{stage}"
        if tag not in warnings:
            warnings.append(tag)


def _needs_confirm(fid: str, mini: dict | None, sel, gate_scores: dict, thresholds: dict) -> bool:
    if fid in sel.hazard_frames:
        return True
    if mini is None:
        return False
    present = mini["present"]
    if any(present.get(c) in {"yes", "unsure"} for c in DECISION):
        return True
    # unsure+gate: a gate class above its stored threshold while mini said no.
    # B3 cascade_eval consults SigLIP only. Other models are used when SigLIP was not run.
    tables = [gate_scores["siglip"]] if "siglip" in gate_scores else list(gate_scores.values())
    for model_scores in tables:
        row = model_scores.get(fid) or {}
        for gate_cls, scene_cls in (
            ("meter", "electric_meter"),
            ("panel", "main_panel"),
            ("gas_meter", "gas_meter"),
            ("ac_condenser", "ac_condenser"),
            ("pool", "pool"),
        ):
            th = thresholds.get(gate_cls)
            if th is None:
                continue
            if row.get(gate_cls, 0) >= th and present.get(scene_cls) == "no":
                return True
    return False


def _gpt_of(cand: Candidate, confirm: dict, mini: dict) -> str:
    for src in (confirm, mini):
        row = src.get(cand.best_frame)
        if row and cand.cls in row["present"]:
            return row["present"][cand.cls]
    return "unsure"


def _detector_candidates(sel, detections: dict, cfg: ObjectsConfig) -> list[Candidate]:
    grouped: dict[str, Candidate] = {}
    for fid in sel.frames:
        for det in _as_dets(detections.get(fid, [])):
            if det["score"] < cfg.proposer_support_thr:
                continue
            cand = grouped.setdefault(det["label"], Candidate(det["label"], [], {}, {}))
            cand.frames.append(fid)
            cand.all_boxes.append((fid, det["box"], det["score"]))
            if det["score"] >= cand.scores.get(fid, -1.0):
                cand.boxes[fid] = det["box"]
                cand.scores[fid] = det["score"]
            cand.confidence = 0.5 if det["score"] >= 0.5 else 0.3
            cand.state = "detector"
            cand.best_frame = max(cand.scores, key=cand.scores.get)
    return list(grouped.values())


def _candidates_from_answers(sel, mini, confirm, detections, cfg: ObjectsConfig) -> list[Candidate]:
    classes: set[str] = set()
    for fid in sel.frames:
        final = confirm.get(fid) or mini.get(fid)
        if not final:
            continue
        for cls, ans in final["present"].items():
            if ans == "yes":
                classes.add(cls)
    out: list[Candidate] = []
    for cls in sorted(classes):
        yes_frames = []
        for fid in sel.frames:
            final = confirm.get(fid) or mini.get(fid)
            if final and final["present"].get(cls) == "yes":
                yes_frames.append(fid)
        boxes: dict[str, list[float]] = {}
        scores: dict[str, float] = {}
        kept: list[tuple[str, list[float], float]] = []
        for fid, dets in detections.items():
            for det in _as_dets(dets):
                if det["label"] != cls:
                    continue
                seeded = fid in yes_frames and det["score"] >= cfg.proposer_seed_thr
                support = det["score"] >= cfg.proposer_support_thr
                if not seeded and not support:
                    continue
                kept.append((fid, det["box"], det["score"]))
                if det["score"] >= scores.get(fid, -1.0):
                    boxes[fid] = det["box"]
                    scores[fid] = det["score"]
        best = max(yes_frames, key=lambda f: scores.get(f, 0.0)) if yes_frames else ""
        conf = 0.75 if cfg.second_reader == "off" else 0.5
        if cls not in DECISION:
            conf = 0.75
        out.append(
            Candidate(cls, yes_frames, boxes, scores, confidence=conf, state="yes", best_frame=best, all_boxes=kept)
        )
    return out


def _as_dets(dets) -> list[dict]:
    rows = []
    for det in dets:
        if isinstance(det, dict):
            rows.append(det)
        else:
            rows.append({"label": det.label, "score": det.score, "box": det.box})
    return rows


def final_answer(mini: dict | None, gpt: dict | None, *, escalate: bool) -> dict | None:
    """Cached-answer cascade: GPT-5.5 wins on escalated frames, else mini."""
    if escalate:
        return gpt or mini
    return mini
