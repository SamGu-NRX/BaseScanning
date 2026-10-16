"""Placement judge. Any flag downgrades a pass to review. Numbers in the answer are ignored."""

from __future__ import annotations

from server.criteria.c3.client import answer_cost, answer_usage, chat, parsed_answer

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
    payload, unparseable = parsed_answer(answer)
    if unparseable or not payload:
        return []
    hit = []
    for name in FLAGS:
        row = payload.get(name) or {}
        if isinstance(row, dict) and row.get("flag"):
            hit.append(name)
    return hit


def _call_record(answer, n_images: int) -> dict:
    payload, _unparseable = parsed_answer(answer)
    model = answer.get("model") if isinstance(answer, dict) else None
    return {
        "model": model or "gpt-5.5",
        "n_images": n_images,
        "cost_usd": answer_cost(answer),
        "usage": answer_usage(answer),
        "answer": payload,
    }


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
        return _clamp(decision, flags, may_reject)
    _payload, unparseable = parsed_answer(answer)
    if unparseable:
        # Leave the pipeline decision alone. judge_required is applied by the caller.
        return None, ["judge_unavailable"]
    flags = flags_from(answer)
    decision = "reject" if flags else "pass"
    return _clamp(decision, flags, may_reject)


def _clamp(decision: str, flags: list[str], may_reject: bool) -> tuple[str, list[str]]:
    if decision == "reject" and not may_reject:
        decision = "manual_review"
    return decision, [f"judge:{flag}" for flag in flags]


def resolve_judge(
    cfg, chat_fn=None, images: list[dict] | None = None, spend=None
) -> tuple[str | None, list[str], dict]:
    """Judge decision, reason codes, and the sidecar record.

    Live `chat` runs only when C3 and external vision are both on and no stub is set.
    `images` are the overlaid keyframes for this profile.
    """
    photos = list(images or [])
    if not getattr(cfg, "c3", True) or not getattr(cfg, "c3_judge", True):
        return None, [], disabled_record()
    stub = getattr(cfg, "judge_stub", None)
    if stub is None and not getattr(cfg, "external_vision", False):
        return None, [], disabled_record()
    if stub is None:
        if spend is not None and spend.blocked():
            return None, ["judge_unavailable"], {"status": "budget", "calls": [], "cost_usd": 0.0}
        fn = chat_fn or chat
        try:
            answer = fn("gpt-5.5", PROMPT, photos)
        except Exception:
            return None, ["judge_unavailable"], {"status": "unavailable", "calls": [], "cost_usd": 0.0}
        cost = answer_cost(answer)
        if spend is not None:
            spend.charge(cost)
        decision, reasons = _from_answer(answer, bool(getattr(cfg, "judge_may_reject", False)))
        if "judge_unavailable" in reasons and getattr(cfg, "judge_required", False):
            decision = "manual_review"
        status = "unavailable" if "judge_unavailable" in reasons else "ok"
        return (
            decision,
            reasons,
            {"status": status, "calls": [_call_record(answer, len(photos))], "cost_usd": cost},
        )
    decision, reasons = _from_answer(stub, bool(getattr(cfg, "judge_may_reject", False)))
    return decision, reasons, {"status": "stub", "calls": [], "cost_usd": 0.0}


def judge_if_pass(decision: str, cfg, images: list[dict] | None = None, chat_fn=None, spend=None):
    """One judge call per profile, and only when that profile's placement passed."""
    if decision != "pass" or not getattr(cfg, "c3", True) or not getattr(cfg, "c3_judge", True):
        return None, [], None
    return resolve_judge(cfg, chat_fn=chat_fn, images=images, spend=spend)
