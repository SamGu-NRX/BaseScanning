"""Collect score-verdicts outputs for every site x scene variant x engine into one table.

    python collect.py OUT.json OUT.md
"""

import json
import sys
from pathlib import Path

W = Path.home() / "e2e/work"
rows = []
for site_dir in sorted(W.iterdir()):
    vd = site_dir / "verdicts"
    if not vd.exists():
        continue
    for tag_dir in sorted(vd.iterdir()):
        for f in sorted((tag_dir / "scores").glob("*.json")):
            r = json.loads(f.read_text())
            sr, st, h = r["start_range"], r["site"], r["headline"]
            pos = sr["positions"]
            n = sum(pos.values()) - pos.get("unscored", 0) - pos.get("not_evaluated", 0)
            v = f.name.split("__eth3d")[0]
            eng = ("C1pr11" if v.startswith("C1pr11") else "C2" if v.startswith("C2") else "C1-3d")
            rows.append({
                "site": site_dir.name, "variant": tag_dir.name, "engine": eng, "profile": r["labels_profile"],
                "unsafe_pass_positions": h["unsafe_pass_positions"], "unsafe_site": h["unsafe_site_decision"],
                "contract_unsafe": h["coverage_contract_unsafe_passes"],
                "false_reject_positions": pos.get("false_rejection", 0), "missed_review_positions": pos.get("missed_review", 0),
                "over_caution_positions": pos.get("over_caution", 0), "scored_positions": n,
                "start_agreement": sr["agreement"], "start_manual_review_rate": sr["manual_review_rate"],
                "site_label": st["decision"]["label"], "site_verdict": st["decision"]["verdict"], "site_result": st["decision"]["result"],
                "c5_errors": len(r.get("c5_margin_errors", [])),
            })
Path(sys.argv[1]).write_text(json.dumps(rows, indent=1))
cols = ["site", "variant", "engine", "profile", "site_verdict", "site_label", "site_result", "unsafe_pass_positions",
        "unsafe_site", "false_reject_positions", "start_agreement", "start_manual_review_rate", "scored_positions"]
md = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
for r in rows:
    md.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
Path(sys.argv[2]).write_text("\n".join(md) + "\n")
print("\n".join(md))
