"""Per site x variant x engine: site decision, decidability, label score and flips vs the laser verdict.

    python summary.py OUT.md OUT.json
"""

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from decide import decide  # noqa: E402
from flips import engines  # noqa: E402

W = Path.home() / "e2e/work"
flips = json.loads((Path.home() / "e2e/out/flips.json").read_text())
scores = json.loads((Path.home() / "e2e/out/scores.json").read_text())
SITES = {"facade": "eth3d-facade", "electro": "eth3d-electro", "delivery_area": "eth3d-delivery"}
PROF = {"C1pr11.demo": "demo", "C1-3d.A": "A", "C1-3d.B": "B", "C1-3d.C": "C", "C1-3d.S": "S", "C2.demo": "demo", "C2.S": "S"}
rows = []
for key, site in SITES.items():
    base = W / key / "verdicts"
    for vdir in sorted(base.iterdir()):
        E = engines(vdir)
        for eng in ["C1pr11.demo", "C1-3d.A", "C1-3d.B", "C1-3d.C", "C1-3d.S", "C2.demo", "C2.S"]:
            r = {"site": key, "variant": vdir.name, "engine": eng}
            if eng not in E:
                r["status"] = "no verdict (no scene: meter wall not reconstructed)" if not (vdir / "C1pr11.demo.json").exists() else "not run"
                rows.append(r)
                continue
            v = json.loads(E[eng].read_text())
            d = decide(v)
            r.update({"site_decision": d["site"], "positions": d["positions"], "pos_pass": d.get("pos_pass"), "pos_fail": d.get("pos_fail"),
                      "pos_unsure": d.get("pos_unsure"), "cells_decided": d.get("cells_decided")})
            eng_s = "C1pr11" if eng.startswith("C1pr11") else ("C2" if eng.startswith("C2") else "C1-3d")
            sc = [s for s in scores if s["site"] == key and s["variant"] == vdir.name and s["engine"] == eng_s and s["profile"] == PROF[eng]]
            if sc:
                s = sc[0]
                r.update({k: s[k] for k in ("site_label", "site_result", "unsafe_pass_positions", "unsafe_site", "false_reject_positions",
                                             "missed_review_positions", "start_agreement", "start_manual_review_rate", "scored_positions")})
            fl = flips.get(f"{site}|{eng}|{vdir.name}")
            if fl:
                cause, kinds, tot = Counter(), Counter(), 0
                for c, x in fl["per_criterion"].items():
                    cause.update(x["causes"])
                    for k, n in x["kinds"].items():
                        a, b = k.split("->")
                        kinds["decisive->unsure" if a in ("pass", "fail") and b == "unsure" else
                              "unsure->decisive" if a == "unsure" and b in ("pass", "fail") else
                              "pass<->fail" if {a, b} == {"pass", "fail"} else "missing" if b == "none" else "other"] += n
                cells = fl["positions"] * fl["criteria_compared"]
                r.update({"flip_cells_share": round(sum(kinds.values()) / max(cells, 1), 3), "flip_kinds": dict(kinds),
                          "flip_causes": dict(cause), "overall_flip_share": fl["overall_flip_share"],
                          "crit_flipped_over_10pct": sum(1 for x in fl["per_criterion"].values() if x["share"] > 0.10),
                          "criteria_compared": fl["criteria_compared"]})
            rows.append(r)
Path(sys.argv[2]).write_text(json.dumps(rows, indent=1))
cols = ["site", "variant", "engine", "site_decision", "site_label", "site_result", "unsafe_pass_positions", "false_reject_positions",
        "start_agreement", "pos_pass", "pos_fail", "pos_unsure", "cells_decided", "crit_flipped_over_10pct", "criteria_compared",
        "flip_cells_share", "flip_kinds", "flip_causes", "status"]
md = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
for r in rows:
    md.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
Path(sys.argv[1]).write_text("\n".join(md) + "\n")
print(len(rows))
