"""Panel cascade: mini first, GPT-5.5 only on risky reads. Hazard `confirmed` needs both models."""

from __future__ import annotations

import re

from server.reads.types import PanelRead, ReaderAnswer

# Escalation and the B3 hazard claim use this set (includes pushmatic).
HAZARD_LINES = {"federal_pacific_stab_lok", "zinsco_sylvania_magnetrip", "challenger", "pushmatic"}
# `confirmed` is only Base's recall list. Agreed pushmatic stays a review hint.
BASE_RECALL = {"federal_pacific_stab_lok", "zinsco_sylvania_magnetrip", "challenger"}
HAZARD_TEXT = re.compile(
    r"stab-?lok|federal\s*pacific|zinsco|sylvania|magnetrip|pushmatic|challenger",
    re.IGNORECASE,
)
BRANDS = {
    "federal_pacific": r"federal\s*(?:pacific|pioneer)|\bfpe\b|stab-?lok",
    "zinsco": r"zinsco|sylvania|\bgte\b|magnetrip",
    "square_d": r"square\s*d|schneider|\bqo\b|homeline|multi-?breaker",
    "eaton": r"eaton|cutler|\bch\b",
    "ge": r"\bge\b|general\s*electric",
    "siemens": r"siemens|\bite\b|murray",
    "westinghouse": r"westinghouse",
    "crouse_hinds": r"crouse",
    "frank_adam": r"frank\s*adam",
    "indoasian": r"indo\s*asian",
    "pushmatic": r"pushmatic|bulldog",
    "challenger": r"challenger",
    "other": r".",
}


def brand_of(text: str | None) -> str | None:
    if not text:
        return None
    for key, pattern in BRANDS.items():
        if re.search(pattern, text, re.IGNORECASE):
            return key
    return None


def as_int(value: object) -> int | None:
    try:
        return int(str(value).strip().rstrip("Aa"))
    except (TypeError, ValueError):
        return None


def claims(answer: dict | None) -> dict:
    answer = answer or {}
    text = " ".join(str(answer.get(key) or "") for key in ("manufacturer", "product_line")).strip()
    return {
        "brand": brand_of(text),
        "main": as_int(answer.get("main_breaker_amps")),
        "rating": as_int(answer.get("panel_rating_amps")),
        "hazard": answer.get("hazard_line") in HAZARD_LINES,
        "line": answer.get("hazard_line"),
        "evidence": answer.get("brand_evidence"),
        "legibility": answer.get("legibility"),
    }


def should_escalate(answer: dict | None, *, force: bool = False) -> bool:
    """Autopass forces a GPT-5.5 read (SD-50). Otherwise the B3 risky-amps predicate."""
    if force:
        return True
    return needs_escalation(answer)


def needs_escalation(answer: dict | None) -> bool:
    """Mini's answer is risky when it claims a hazard, an unprinted brand, amps, or legible text with no amps."""
    row = claims(answer)
    if row["hazard"]:
        return True
    if row["brand"] and row["evidence"] != "printed_text":
        return True
    if row["main"] is not None or row["rating"] is not None:
        return True
    return row["legibility"] == "good" and row["main"] is None and row["rating"] is None


def hazard_evidence(answer: dict | None) -> str:
    answer = answer or {}
    parts = [str(answer.get(key) or "") for key in ("manufacturer", "product_line")]
    parts.extend(str(item) for item in (answer.get("text_seen") or []))
    return "printed" if HAZARD_TEXT.search(" ".join(parts)) else "style_only"


def _review_hints(mini: dict | None, gpt: dict | None) -> list[str]:
    hints: list[str] = []
    texts = []
    for answer in (mini, gpt):
        if not answer:
            continue
        texts.append(
            " ".join(
                [str(answer.get("manufacturer") or ""), str(answer.get("product_line") or "")]
                + [str(item) for item in (answer.get("text_seen") or [])]
            )
        )
        line = answer.get("hazard_line")
        if line == "pushmatic" and "pushmatic" not in hints:
            hints.append("pushmatic")
    blob = " ".join(texts)
    if re.search(r"westinghouse", blob, re.IGNORECASE) and "westinghouse" not in hints:
        hints.append("westinghouse")
    if re.search(r"federal\s*pioneer", blob, re.IGNORECASE):
        hints.append("federal_pioneer")
    rusts = [((answer or {}).get("condition") or {}).get("rust") for answer in (mini, gpt) if answer]
    if len(rusts) >= 2 and all(item == "heavy" for item in rusts):
        hints.append("rust_heavy")
    damages = [((answer or {}).get("condition") or {}).get("damage_or_scorching") for answer in (mini, gpt) if answer]
    if len(damages) >= 2 and all(item is True for item in damages):
        hints.append("damage_or_scorching")
    return hints


def _line_in(answer: dict | None, group: set[str]) -> bool:
    return bool(answer) and answer.get("hazard_line") in group


def decide_panel(mini: ReaderAnswer | None, gpt: ReaderAnswer | None) -> PanelRead:
    mini_ok = mini is not None and mini.error is None and mini.answer is not None
    gpt_ok = gpt is not None and gpt.error is None and gpt.answer is not None
    mini_answer = mini.answer if mini_ok and mini is not None else None
    gpt_answer = gpt.answer if gpt_ok and gpt is not None else None
    mini_claims = claims(mini_answer)
    gpt_claims = claims(gpt_answer) if gpt_ok else None
    escalated = gpt_ok
    if escalated and gpt_claims is not None:
        brand = gpt_claims["brand"] or mini_claims["brand"]
        main = gpt_claims["main"]
        rating = gpt_claims["rating"]
        evidence = (gpt_answer or {}).get("brand_evidence") or mini_claims["evidence"]
        source = gpt_answer or mini_answer or {}
    else:
        brand = mini_claims["brand"]
        main = mini_claims["main"]
        rating = mini_claims["rating"]
        evidence = mini_claims["evidence"]
        source = mini_answer or {}

    mini_base = _line_in(mini_answer, BASE_RECALL)
    gpt_base = _line_in(gpt_answer, BASE_RECALL)
    if mini_ok and gpt_ok:
        if mini_base and gpt_base:
            hazard = "confirmed"
            line = (gpt_answer or {}).get("hazard_line")
        elif mini_base or gpt_base:
            hazard = "disputed"
            line = (gpt_answer or mini_answer or {}).get("hazard_line")
        else:
            hazard = "none"
            line = (gpt_answer or mini_answer or {}).get("hazard_line")
    elif gpt_ok and gpt_base:
        hazard = "confirmed"
        line = (gpt_answer or {}).get("hazard_line")
    elif mini_ok and mini_base and not gpt_ok:
        hazard = "confirmed"
        line = (mini_answer or {}).get("hazard_line")
    elif not mini_ok and not gpt_ok:
        hazard = "unknown"
        line = None
    else:
        hazard = "none"
        line = (gpt_answer or mini_answer or {}).get("hazard_line")

    condition = source.get("condition") or {}
    return PanelRead(
        equipment=source.get("equipment"),
        brand=brand,
        brand_evidence=evidence,
        product_line=source.get("product_line"),
        main_breaker_amps=main,
        panel_rating_amps=rating,
        hazard=hazard,  # type: ignore[arg-type]
        hazard_line=line if isinstance(line, str) else None,
        review_hints=_review_hints(mini_answer, gpt_answer),
        rust=condition.get("rust"),
        damage_or_scorching=condition.get("damage_or_scorching"),
        enclosures_visible=as_int(source.get("enclosures_visible")),
        mains_visible=as_int(source.get("mains_visible")),
        text_seen=[str(item) for item in (source.get("text_seen") or [])][:10],
        legibility=source.get("legibility"),
        hazard_evidence=hazard_evidence(source),
        hazard_line_mini=(mini_answer or {}).get("hazard_line")
        if isinstance((mini_answer or {}).get("hazard_line"), str)
        else None,
        hazard_line_gpt=(gpt_answer or {}).get("hazard_line")
        if isinstance((gpt_answer or {}).get("hazard_line"), str)
        else None,
        brand_mini=mini_claims["brand"],
        brand_gpt=gpt_claims["brand"] if gpt_claims else None,
        evidence_mini=mini_claims["evidence"] if isinstance(mini_claims["evidence"], str) else None,
        evidence_gpt=(gpt_answer or {}).get("brand_evidence") if gpt_ok else None,
        gpt_read=gpt_ok,
    )


def b3_hazard(mini_answer: dict | None, gpt_answer: dict | None, escalated: bool) -> bool:
    """The measured verify_risky_amps_legible hazard bit (5/5, 0 false alarms)."""
    if escalated:
        return claims(gpt_answer)["hazard"]
    return claims(mini_answer)["hazard"]
