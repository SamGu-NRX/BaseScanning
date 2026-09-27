"""Placement judge. Any flag downgrades a pass to review. Numbers in the answer are ignored."""

from __future__ import annotations

from server.criteria.c3.client import chat

FLAGS = (
    "missed_object",
    "spot_not_visible",
    "meter_not_visible_or_uncertain",
    "street_facing_front",
    "low_resolution",
    "other_rule_violation",
)

PROMPT = (
    "You are checking one battery placement drawn on the photos. "
    "Answer JSON only. For each key missed_object, spot_not_visible, "
    "meter_not_visible_or_uncertain, street_facing_front, low_resolution, "
    "other_rule_violation give {flag: bool, note: string}. Do not estimate lengths."
)


def flags_from(answer: dict | None) -> list[str]:
    if not answer:
        return []
    hit = []
    for name in FLAGS:
        row = answer.get(name) or {}
        if isinstance(row, dict) and row.get("flag"):
            hit.append(name)
    return hit


def disabled_record() -> dict:
    return {"status": "disabled", "calls": [], "cost_usd": 0.0}


def _from_answer(answer, may_reject: bool) -> tuple[str | None, list[str]]:
    if answer is None:
        return None, []
    if isinstance(answer, str):
        if answer not in {"pass", "manual_review", "reject"}:
            return None, []
        decision = answer
        flags = ["reject"] if answer == "reject" else []
    else:
        flags = flags_from(answer)
        decision = "reject" if flags else "pass"
    if decision == "reject" and not may_reject:
        decision = "manual_review"
    return decision, [f"judge:{flag}" for flag in flags]


def resolve_judge(cfg, chat_fn=None) -> tuple[str | None, list[str], dict]:
    """Judge decision, reason codes, and the sidecar record.

    Live `chat` runs only when C3 and external vision are both on and no stub is set.
    """
    if not getattr(cfg, "c3", True) or not getattr(cfg, "c3_judge", True):
        return None, [], disabled_record()
    stub = getattr(cfg, "judge_stub", None)
    if stub is None and not getattr(cfg, "external_vision", False):
        return None, [], disabled_record()
    if stub is None:
        fn = chat_fn or chat
        try:
            answer = fn("gpt-5.5", PROMPT, [])
        except Exception:
            return None, ["judge_unavailable"], {"status": "unavailable", "calls": [], "cost_usd": 0.0}
        decision, reasons = _from_answer(answer, bool(getattr(cfg, "judge_may_reject", False)))
        return decision, reasons, {"status": "ok", "calls": [{"model": "gpt-5.5"}], "cost_usd": 0.0}
    decision, reasons = _from_answer(stub, bool(getattr(cfg, "judge_may_reject", False)))
    return decision, reasons, {"status": "stub", "calls": [], "cost_usd": 0.0}
