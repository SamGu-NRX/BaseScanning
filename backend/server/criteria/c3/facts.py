"""Site facts. Photo judgements never fail on their own. Auto-pass stays off."""

from __future__ import annotations

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
