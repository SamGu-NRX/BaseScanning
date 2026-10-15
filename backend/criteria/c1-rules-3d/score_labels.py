"""Score C1 verdicts' site `placement` criterion against internal/criteria/labels-v0 (base-labels/v0),
with the contract's section 6.2 definitions. The harness has no score-verdicts command yet, so this
covers the site-scope subset the labels contain.

truth pass -> verdict must be pass; fail -> fail; borderline -> unsure; not_pass -> fail or unsure;
unknown -> not scored (reported). Site-level unsafe pass: verdict pass where the label's
site_decision is manual_review or reject.

    python score_labels.py outputs/verdicts/*.json
"""

import json
import sys
from pathlib import Path

LABELS = Path("/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/criteria/labels-v0")
OK = {"pass": {"pass"}, "fail": {"fail"}, "borderline": {"unsure"}, "not_pass": {"fail", "unsure"}}


def main(paths):
    rows = []
    for p in paths:
        v = json.loads(Path(p).read_text())
        site, prof = v["scene"]["site"], v["evaluator"]["rules_profile"]
        lf = LABELS / f"{site}.{prof}.json"
        if not lf.exists():
            continue
        lab = json.loads(lf.read_text())
        truth = next(x for x in lab["labels"] if x["criterion"] == "placement")
        crit = next(c for c in v["criteria"] if c["id"] == "placement")
        t = truth["truth"]
        scored = t in OK
        rows.append({
            "verdict": v["verdict_id"], "site": site, "profile": prof, "decision": v["site"]["decision"],
            "label_site_decision": lab["site_decision"], "placement_truth": t, "placement_outcome": crit["outcome"],
            "scored": scored, "agree": (crit["outcome"] in OK[t]) if scored else None,
            "unsafe_pass": v["site"]["decision"] == "pass" and lab["site_decision"] in ("manual_review", "reject"),
            "over_caution": scored and crit["outcome"] == "unsure" and t in ("pass", "fail"),
            "false_rejection": scored and crit["outcome"] == "fail" and t == "pass",
        })
    return rows


if __name__ == "__main__":
    rows = main(sys.argv[1:])
    out = Path(__file__).parent / "outputs" / "label-scores.json"
    out.write_text(json.dumps(rows, indent=1))
    for r in rows:
        print(f"{r['verdict']:60s} {r['decision']:13s} truth={r['placement_truth']:10s} "
              f"agree={r['agree']} unsafe_pass={r['unsafe_pass']}")
    sc = [r for r in rows if r["scored"]]
    print(f"\n{len(rows)} verdicts with labels; {len(sc)} scored; agree {sum(r['agree'] for r in sc)}/{len(sc)}; "
          f"site unsafe passes {sum(r['unsafe_pass'] for r in rows)}")
