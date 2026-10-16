"""Score C3 results against the draft labels (internal/criteria/house-labels.json).

python3 score.py [--variants photo_only_verbatim,photo_only]  -> prints tables, writes results/scores.json

Per (variant, house, model): criteria agreeing with acceptable_verdicts, unsafe passes (pass / "no" / clean
read the label doesn't accept), wrong rejects, review-like share of the 16 verdict slots, cost.
The reconstructed-prompt runs in photo_only/ use a different schema and are not scored.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import ssr_prompts as S
import vlm

LABELS = Path(__file__).resolve().parents[2] / "criteria" / "house-labels.json"
PASSY = {"pass", "no"}


def load(house: str, variant: str, model: str) -> dict[str, dict]:
    d = vlm.RESULTS / house / variant / model
    return {p.stem: json.loads(p.read_text()) for p in sorted(d.glob("p*.json"))} if d.exists() else {}


FEATURE = {"p04": "meter_number", "p03": "cl320", "p02": "oversized_can", "p11": "meter_type", "p10": "meter_mount",
           "p01": "multiple_mains", "p05": "meter_panel_relationship"}
DECISION = {"p06": "placement.A", "p07": "placement.B", "p08": "placement.C", "p09": "main_panel_type", "p13": "panel_recall"}
P12 = {"meterNotAccessible": "meter_not_accessible", "panelNotAccessible": "panel_not_accessible", "panelInHouse": "panel_in_house", "notSameWall": "not_same_wall"}
# Keys compared for feature verdicts (labels v0.2 use the verbatim prompts' field names); secondary fields are ignored.
PRIMARY = ("meter_number", "clean-decision", "cl320Label", "meterCanLabel", "type", "multipleMainPanelLabel", "meterPanelRelationship")
UNCLEAR = {"cl320_unclear", "meter_can_unclear", "unclear_or_insufficient_evidence", "insufficient_evidence", "unknown", "not_applicable"}


def to_label_space(res: dict[str, dict]) -> dict[str, object]:
    """Feature prompts -> their raw JSON; decisions -> the decision string; P12 -> four verdict strings; plus the site composite."""
    j = {k[:3]: v["json"] for k, v in res.items() if v.get("json")}  # errored prompts are left unscored
    out: dict[str, object] = {}
    for p, crit in FEATURE.items():
        if p in j:
            out[crit] = j[p]
    for p, crit in DECISION.items():
        if p in j:
            out[crit] = j[p].get("decision") or "manual_review"
    if "p12" in j:
        for src, crit in P12.items():
            item = j["p12"].get(src) or {}
            out[crit] = (item.get("verdict") if isinstance(item, dict) else item) or "manual_review"
    if all(k[:3] in j for k in res):
        out["site"] = site(out)
    return out


def site(o: dict) -> str:
    """Spec section 1 composite with profile C placement: reject if a deciding check rejects; pass only if every deciding check passes and mount/type reads are clean."""
    dec = [o.get(k) for k in ("placement.C", "main_panel_type", "panel_recall")]
    p12 = [o.get(k) for k in ("meter_not_accessible", "panel_not_accessible", "panel_in_house")]
    if "reject" in dec or "yes" in p12:
        return "reject"
    clean = all(isinstance(o.get(k), dict) and o[k].get("clean-decision") is True for k in ("meter_type", "meter_mount"))
    if all(v == "pass" for v in dec) and all(v == "no" for v in p12 + [o.get("not_same_wall")]) and clean:
        return "pass"
    return "manual_review"


def matches(pred, acc) -> bool:
    if not isinstance(acc, dict):
        return pred == acc
    if not isinstance(pred, dict):
        return False
    for k in PRIMARY:
        if k not in acc:
            continue
        if k == "meter_number":
            got = str(pred.get(k) or "").strip()
            if (acc[k] == "<any>" and not got) or (acc[k] != "<any>" and got != acc[k]):
                return False
        elif pred.get(k) != acc[k]:
            return False
    return True


def unsafe(pred) -> bool:
    """A confident answer: pass / "no", a clean read, or a definite feature label with sufficient evidence."""
    if isinstance(pred, dict):
        if pred.get("clean-decision") is True:
            return True
        lab = next((pred.get(k) for k in ("cl320Label", "meterCanLabel", "multipleMainPanelLabel", "meterPanelRelationship") if k in pred), None)
        return lab is not None and lab not in UNCLEAR and not pred.get("insufficientEvidence") and pred.get("evidenceSufficiency") != "insufficient"
    return pred in PASSY


def score(variant: str, labels: dict) -> list[dict]:
    rows = []
    for d in sorted((vlm.RESULTS).glob(f"*/{variant}/*")):
        house, model = d.parent.parent.name, d.name
        res = load(house, variant, model)
        if len(res) < 13:
            continue
        pred = to_label_space(res)
        slots = {k: v for r in res.values() for k, v in (r.get("verdicts") or {}).items()}
        row = {"variant": variant, "house": house, "model": model, "cost_usd": round(sum(r.get("cost_usd", 0) for r in res.values()), 3),
               "review_like": sum(S.is_review_like(v) or v == "insufficient_evidence" for v in slots.values()), "slots": len(slots),
               "latency_s": round(sum(r.get("latency_s", 0) for r in res.values()), 1), "pred": pred}
        crit = labels.get(house, {}).get("criteria")
        if crit:
            ok, bad = [], []
            for k, lab in crit.items():
                if k == "placement.S" or k not in pred:
                    continue
                (ok if any(matches(pred[k], a) for a in lab["acceptable_verdicts"]) else bad).append(k)
            row.update(scored=len(ok) + len(bad), agree=len(ok), disagree=bad,
                       unsafe_pass=[k for k in bad if unsafe(pred[k])],
                       wrong_reject=[k for k in bad if pred[k] in ("reject", "yes")])
        rows.append(row)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="photo_only_verbatim")
    a = ap.parse_args()
    labels = json.loads(LABELS.read_text())["houses"]
    allrows = []
    for v in a.variants.split(","):
        rows = score(v, labels)
        allrows += rows
        print(f"\n== {v}")
        agg = defaultdict(lambda: [0, 0, 0, 0.0, 0, 0])
        for r in rows:
            lab = f"{r.get('agree', '-')}/{r.get('scored', '-')}"
            print(f"{r['house']:16} {r['model']:16} agree {lab:6} review {r['review_like']:2}/{r['slots']} ${r['cost_usd']:.2f} "
                  f"unsafe {r.get('unsafe_pass', '-')} wrong_reject {r.get('wrong_reject', '-')} site={r['pred'].get('site', 'n/a (errored prompt)')}")
            if "scored" in r:
                a_ = agg[r["model"]]
                a_[0] += r["agree"]; a_[1] += r["scored"]; a_[2] += len(r["unsafe_pass"]); a_[3] += r["cost_usd"]; a_[4] += 1; a_[5] += len(r["wrong_reject"])
        for m, (ag, sc, un, c, n, wr) in agg.items():
            print(f"  TOTAL {m:16} agree {ag}/{sc} ({ag / sc:.0%}) unsafe passes {un} wrong rejects {wr} mean cost/house ${c / n:.2f} over {n} labelled houses")
    (vlm.RESULTS / "scores.json").write_text(json.dumps(allrows, indent=1, default=str))


if __name__ == "__main__":
    main()
