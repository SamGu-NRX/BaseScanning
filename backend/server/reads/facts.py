"""Turn a reads document into verdict-v0 criterion items. Coverage uses class placeholders."""

from __future__ import annotations

import json

from server.contracts.verdict import verdict_contract_errors
from server.reads.config import ReadsConfig
from server.reads.panel_policy import BASE_RECALL, HAZARD_LINES
from server.reads.types import MeterRead, PanelRead, Reads

_cfg = ReadsConfig()


def coverage_entry(
    *,
    status: str,
    images: list[str],
    element: str,
    view_angle: float | None,
    views_median: float,
    probes: int,
) -> dict:
    return {
        "region": {
            "element_ids": [element],
            "images": images,
            "description": f"{element} close-up",
        },
        "status": status,
        "probes": probes,
        "observed_share": 1.0 if status == "observed" else (0.5 if status == "partial" else 0.0),
        "seen_share": 0.0 if status == "unobserved" else 1.0,
        "views_min": int(views_median) if status == "observed" else 0,
        "views_median": views_median,
        "best_incidence_deg": view_angle,
        "worst_best_incidence_deg": None,
        "rule": {
            "min_views": 1,
            "surface": "free",
            "max_incidence_deg": None,
            "observed_share": 1.0,
        },
        "method": "reads/v0",
    }


def _legible_count(legibility: dict[str, str]) -> int:
    return sum(1 for value in legibility.values() if value != "poor")


def meter_coverage_status(meter: MeterRead) -> str:
    if meter.status == "disabled":
        return "unobserved"
    count = _legible_count(meter.legibility)
    if count >= 1 and any(value != "poor" for value in meter.legibility.values()):
        if count >= 2 or (len(meter.legibility) <= 1 and count == 1):
            return "observed"
        if count == 1 and len(meter.legibility) >= 2:
            return "partial"
        return "observed"
    if meter.status == "unreadable":
        return "unobserved"
    return "partial" if meter.legibility else "unobserved"


def _view(kind: str, target: str, criteria: list[str], why: str, view_id: str) -> dict:
    return {"id": view_id, "kind": kind, "target": target, "criteria": criteria, "why": why}


def meter_fact(
    meter: MeterRead,
    images: list[str],
    views_median: float,
    view_angle: float | None,
    *,
    confirmed_by: str | None = None,
) -> tuple[dict, dict | None]:
    status = meter_coverage_status(meter)
    if meter.status in {"auto_fill"} and status != "observed":
        status = "observed"
    cov = coverage_entry(
        status=status if meter.status != "unreadable" else "unobserved",
        images=images,
        element="meter",
        view_angle=view_angle,
        views_median=0 if meter.status == "unreadable" else views_median,
        probes=max(1, len(images)),
    )
    view = None
    if meter.status in {"auto_fill"}:
        outcome, cause, clean = "pass", None, True
    elif meter.status == "unreadable":
        outcome, cause, clean = "unsure", "unobserved", False
        view = _view(
            "close_up",
            "meter label close-up",
            ["meter_number"],
            "meter label not legible",
            "rd-meter-close",
        )
    elif meter.status == "disabled":
        outcome, cause, clean = "unsure", "not_evaluated", False
    else:
        outcome, cause, clean = "unsure", "unknown_attribute", False
        view = _view(
            "confirm_attribute",
            "confirm the meter number",
            ["meter_number"],
            "readers disagree or are not barcode-confirmed",
            "rd-meter-confirm",
        )
    item = {
        "id": "meter_number",
        "scope": "site",
        "outcome": outcome,
        "reason": f"meter read {meter.status}",
        "measured": None,
        "threshold": None,
        "fact": {
            "value": meter.value if meter.status != "unreadable" else None,
            "clean": clean,
            "raw": (
                "unreadable"
                if confirmed_by and meter.status == "unreadable"
                else f"confirmed:{confirmed_by}"
                if confirmed_by
                else meter.value or ""
            ),
            "coverage": [cov],
        },
        "evidence": {"element_ids": ["meter"], "views": images, "coverage": cov["status"]},
        "views_needed": [view["id"]] if view else [],
    }
    if cause:
        item["unsure_cause"] = cause
    return item, view


def cl320_fact(meter: MeterRead, images: list[str], views_median: float, view_angle: float | None) -> dict:
    if meter.cl320 == "cl320_unclear" and meter_coverage_status(meter) == "unobserved":
        outcome, cause, clean, status = "unsure", "unobserved", False, "unobserved"
    elif meter.cl320 == "cl320_unclear":
        outcome, cause, clean, status = "unsure", "unknown_attribute", False, "partial"
    else:
        outcome, cause, clean, status = "pass", None, True, "observed"
    cov = coverage_entry(
        status=status,
        images=images,
        element="meter",
        view_angle=view_angle,
        views_median=views_median if status == "observed" else 0,
        probes=max(1, len(images)),
    )
    item = {
        "id": "cl320",
        "scope": "site",
        "outcome": outcome,
        "reason": f"class read {meter.cl320}",
        "measured": None,
        "threshold": None,
        "fact": {
            "value": meter.cl320 if outcome == "pass" else "cl320_unclear",
            "clean": clean,
            "raw": meter.class_label or "",
            "coverage": [cov],
        },
        "evidence": {"element_ids": ["meter"], "views": images, "coverage": cov["status"]},
        "views_needed": [],
    }
    if cause:
        item["unsure_cause"] = cause
    return item


def _flagged_line(line: str | None) -> bool:
    return bool(line) and line in HAZARD_LINES | BASE_RECALL


def _panel_may_pass(panel: PanelRead) -> bool:
    """SD-50/SD-58: autopass needs the measured mini + GPT-5.5 pair, not a single reader."""
    if not panel.gpt_read or panel.hazard != "none" or panel.review_hints:
        return False
    if _flagged_line(panel.hazard_line_mini) or _flagged_line(panel.hazard_line_gpt):
        return False
    if not panel.brand_mini or panel.brand_mini != panel.brand_gpt:
        return False
    if panel.evidence_mini != "printed_text" or panel.evidence_gpt != "printed_text":
        return False
    if panel.brand in BASE_RECALL or panel.brand in {"federal_pacific", "zinsco", "challenger", "pushmatic"}:
        return False
    return True


def panel_fact(panel: PanelRead, images: list[str], cfg: ReadsConfig, *, confirmed_by: str | None = None) -> dict:
    legible = panel.legibility not in {None, "poor"}
    status = "observed" if legible else "unobserved"
    if panel.hazard == "confirmed":
        outcome, cause, clean, value = "unsure", "rule_requires_review", True, f"hazard:{panel.hazard_line}"
    elif panel.hazard == "disputed" or panel.review_hints:
        outcome, cause, clean, value = "unsure", "rule_requires_review", False, panel.hazard_line or "none_identified"
    elif panel.brand and panel.brand_evidence == "printed_text":
        if cfg.panel_autopass and _panel_may_pass(panel):
            outcome, cause, clean, value = "pass", None, True, "none_identified"
            status = "observed"
        else:
            outcome, cause, clean, value = "unsure", "rule_requires_review", True, "none_identified"
    elif not panel.brand:
        outcome, cause, clean = "unsure", "unknown_attribute" if legible else "unobserved", False
        value = None
        status = "partial" if legible else "unobserved"
    else:
        outcome, cause, clean, value = "unsure", "unknown_attribute", False, None
    cov = coverage_entry(
        status=status,
        images=images,
        element="panel",
        view_angle=None,
        views_median=1 if status == "observed" else 0,
        probes=max(1, len(images)),
    )
    raw = (
        f"confirmed:{confirmed_by}"
        if confirmed_by
        else json.dumps(
            {
                "hazard_line_gpt": panel.hazard_line_gpt,
                "hazard_line_mini": panel.hazard_line_mini,
                "hazard_evidence": panel.hazard_evidence,
                "brand": panel.brand,
                "brand_evidence": panel.brand_evidence,
                "main_breaker_amps": panel.main_breaker_amps,
                "panel_rating_amps": panel.panel_rating_amps,
                "text_seen": panel.text_seen,
                "images": images,
                "review_hints": panel.review_hints,
            }
        )
    )
    item = {
        "id": "panel_recall",
        "scope": "site",
        "outcome": outcome,
        "reason": f"panel hazard {panel.hazard}",
        "measured": None,
        "threshold": None,
        "fact": {"value": value, "clean": clean, "raw": raw, "coverage": [cov]},
        "evidence": {"element_ids": ["panel"], "views": images, "coverage": cov["status"]},
        "views_needed": [],
    }
    if cause:
        item["unsure_cause"] = cause
    return item


def to_criteria(reads: Reads, cfg: ReadsConfig | None = None) -> tuple[list[dict], list[dict]]:
    cfg = cfg or _cfg
    items: list[dict] = []
    views: list[dict] = []
    if reads.status == "disabled":
        for criterion_id, element in (("meter_number", "meter"), ("cl320", "meter"), ("panel_recall", "panel")):
            cov = coverage_entry(
                status="unobserved",
                images=[],
                element=element,
                view_angle=None,
                views_median=0,
                probes=0,
            )
            items.append(
                {
                    "id": criterion_id,
                    "scope": "site",
                    "outcome": "unsure",
                    "unsure_cause": "not_evaluated",
                    "reason": "vision calls disabled",
                    "measured": None,
                    "threshold": None,
                    "fact": {"value": None, "clean": False, "raw": "", "coverage": [cov]},
                    "evidence": {"element_ids": [element], "views": [], "coverage": "unobserved"},
                    "views_needed": [],
                }
            )
        return items, views
    if reads.meter is not None and reads.meter.images:
        images = reads.meter.images
        median = 2.0 if "value_from_oblique" not in reads.warnings and len(images) > 1 else float(max(1, len(images)))
        if reads.meter.status == "unreadable":
            median = 0
        confirmed_by = reads.confirm.locks.get("meter_number") if reads.confirm is not None else None
        item, view = meter_fact(reads.meter, images, median, None, confirmed_by=confirmed_by)
        items.append(item)
        if view:
            views.append(view)
        items.append(cl320_fact(reads.meter, images, median if reads.meter.status != "unreadable" else 0, None))
    if reads.panel is not None and reads.panel.merged is not None:
        images = list(reads.panel.per_image)
        panel_by = None
        if reads.confirm is not None:
            panel_by = reads.confirm.locks.get("panel_brand") or reads.confirm.locks.get("main_breaker_amps")
        items.append(panel_fact(reads.panel.merged, images, cfg, confirmed_by=panel_by))
    return items, views


def coverage_contract_errors(verdict: dict) -> list[str]:
    return verdict_contract_errors(verdict)
