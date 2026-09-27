"""Site facts. Photo judgements never fail on their own. Auto-pass stays off."""

from __future__ import annotations

from pathlib import Path

from server.criteria.c3.client import answer_cost, answer_usage, parsed_answer
from server.criteria.c3.client import chat as default_chat
from server.criteria.c3.overlay import outline_data_url
from server.criteria.c3.select_photos import select

FACT_IDS = (
    "oversized_can",
    "meter_type",
    "meter_mount",
    "multiple_mains",
    "main_panel_type",
    "meter_panel_relationship",
    "meter_not_accessible",
    "panel_not_accessible",
    "panel_in_house",
    "not_same_wall",
)

DECISION = {
    "main_panel_type",
    "panel_recall",
    "meter_not_accessible",
    "panel_not_accessible",
    "panel_in_house",
}


FACT_PROMPT = (
    "You are reading site facts from photos. Answer JSON only. "
    "For each id give {value: string, clean: bool}. "
    "Ids: oversized_can, meter_type, meter_mount, multiple_mains, main_panel_type, "
    "meter_panel_relationship, meter_not_accessible, panel_not_accessible, panel_in_house, not_same_wall. "
    "Do not estimate lengths."
)


def fact_photos(scene: dict, scene_dir, photo_ids: list[str]) -> list[dict]:
    """Data-url JPEGs for the stills and keyframes `select` named. Missing files are skipped."""
    if scene_dir is None:
        return []
    root = Path(scene_dir)
    frames = {frame.get("id"): frame for frame in list(scene.get("stills") or []) + list(scene.get("keyframes") or [])}
    photos = []
    for fid in photo_ids:
        frame = frames.get(fid) or {}
        name = frame.get("image")
        if not name:
            continue
        url = outline_data_url(root / name, [], (frame.get("w"), frame.get("h")))
        if url:
            photos.append({"id": fid, "url": url})
    return photos


def _fact_call(answer, n_images: int) -> dict:
    payload, _unparseable = parsed_answer(answer)
    model = answer.get("model") if isinstance(answer, dict) else None
    return {
        "model": model or "gpt-5.5",
        "n_images": n_images,
        "cost_usd": answer_cost(answer),
        "usage": answer_usage(answer),
        "answer": payload,
    }


def evaluate_facts(
    scene: dict, cfg, chat_fn=None, images: list[dict] | None = None, scene_dir=None, spend=None
) -> tuple[list[dict], dict]:
    """Photo facts when C3 and external vision are on. Otherwise every fact stays not_evaluated.

    A clean read is still unsure: auto-pass is off, and a photo judgement never fails.
    """
    if not getattr(cfg, "c3", True) or not getattr(cfg, "c3_facts", True) or not getattr(cfg, "external_vision", False):
        return disabled_facts(), {"status": "disabled", "calls": [], "cost_usd": 0.0, "photos": []}
    photo_ids = select(scene, None, limit=6)
    if images is None:
        payload = fact_photos(scene, scene_dir, photo_ids)
    else:
        payload = list(images)
    if spend is not None and spend.blocked():
        return disabled_facts(), {"status": "budget", "calls": [], "cost_usd": 0.0, "photos": photo_ids}
    fn = chat_fn or default_chat
    try:
        answer = fn("gpt-5.5", FACT_PROMPT, payload)
    except Exception:
        return disabled_facts(), {"status": "unavailable", "calls": [], "cost_usd": 0.0, "photos": photo_ids}
    cost = answer_cost(answer)
    if spend is not None:
        spend.charge(cost)
    model, unparseable = parsed_answer(answer)
    call = _fact_call(answer, len(payload))
    if unparseable or model is None:
        return disabled_facts(), {"status": "unavailable", "calls": [call], "cost_usd": cost, "photos": photo_ids}
    rows = []
    for fid in FACT_IDS:
        row = model.get(fid)
        clean = isinstance(row, dict) and bool(row.get("clean"))
        rows.append(
            {
                "id": fid,
                "scope": "site",
                "outcome": "unsure",
                "unsure_cause": "rule_requires_review" if clean else "unknown_attribute",
                "reason": "photo fact",
                "measured": None,
                "threshold": None,
            }
        )
    return rows, {"status": "ok", "calls": [call], "cost_usd": cost, "photos": photo_ids}


def disabled_facts() -> list[dict]:
    out = []
    for fid in FACT_IDS:
        out.append(
            {
                "id": fid,
                "scope": "site",
                "outcome": "unsure",
                "unsure_cause": "not_evaluated",
                "reason": "c3 disabled",
                "measured": None,
                "threshold": None,
            }
        )
    return out


def apply_reads(scene: dict, reads: dict | None) -> list[dict]:
    """Copy WS-08 facts, remapping class placeholders onto scene object ids."""
    if not reads or reads.get("status") == "disabled":
        return []
    meter_id = scene.get("frame", {}).get("anchor", {}).get("element_id")
    panel = next((o["id"] for o in scene["objects"] if o["class"] == "panel"), None)
    copied = []
    for fact in reads.get("facts") or []:
        item = dict(fact)
        for key in ("measured", "fact"):
            host = item.get(key)
            if not isinstance(host, dict) or not host.get("coverage"):
                continue
            cov = []
            for ev in host["coverage"]:
                ev = dict(ev)
                region = dict(ev.get("region") or {})
                ids = []
                for eid in region.get("element_ids") or []:
                    if eid == "meter" and meter_id:
                        ids.append(meter_id)
                    elif eid == "panel" and panel:
                        ids.append(panel)
                    else:
                        ids.append(eid)
                if ids:
                    region["element_ids"] = ids
                ev["region"] = region
                cov.append(ev)
            item[key] = dict(host)
            item[key]["coverage"] = cov
        copied.append(item)
    return copied
