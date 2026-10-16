"""Decidability of verdicts: share of battery start positions (2 in steps over the verdict's own
candidates) whose overall outcome is pass / fail / unsure, and share of candidate-criterion cells
decided (pass or fail) vs unsure; plus the site decision.

    python decide.py VERDICT.json [...]
"""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from flips import STEP, overall, sample  # noqa: E402


def decide(v: dict) -> dict:
    cands = v.get("candidates", [])
    crit = sorted({c for cd in cands for c in cd.get("failing", []) + cd.get("unsure", [])} |
                  {c["id"] for c in v["criteria"] if c.get("scope") == "candidate"})
    if not cands:
        return {"site": v["site"]["decision"], "positions": 0}
    lo = min(c["start_s_m"][0] for c in cands)
    hi = max(c["start_s_m"][1] for c in cands)
    s = np.arange(lo, hi + 1e-9, STEP)
    o = overall(v, s)
    o = o[o != "none"]
    cells = Counter()
    per = {}
    smp = sample(v, s, crit)
    for c in crit:
        x = smp[c][smp[c] != "none"]
        cc = Counter(x)
        cells.update(cc)
        per[c] = round((cc["pass"] + cc["fail"]) / max(len(x), 1), 3)
    n = max(len(o), 1)
    tot = max(sum(cells.values()), 1)
    return {"site": v["site"]["decision"], "reasons": v["site"].get("reasons"), "positions": int(len(o)),
            "pos_pass": round(float(np.mean(o == "pass")), 3), "pos_fail": round(float(np.mean(o == "fail")), 3),
            "pos_unsure": round(float(np.mean(o == "unsure")), 3),
            "cells_decided": round((cells["pass"] + cells["fail"]) / tot, 3), "per_criterion_decided": per,
            "unsure_causes_at_spot": dict(Counter(c.get("unsure_cause") for c in v["criteria"] if c["outcome"] == "unsure"))}


if __name__ == "__main__":
    for f in sys.argv[1:]:
        print(Path(f).name, json.dumps(decide(json.loads(Path(f).read_text()))))
