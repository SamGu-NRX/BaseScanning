"""Summarise c1_scale_sweep.jsonl per scale bound and map the Monte Carlo bound distribution onto it.

    python analyze_c1.py [results dir]  ->  results/c1_scale_summary.{md,json}
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

R = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent / "results")
rows = [json.loads(l) for l in open(R / "c1_scale_sweep.jsonl")]
levels = sorted({r["bound"] for r in rows})

site = defaultdict(lambda: defaultdict(int))
chk = defaultdict(lambda: defaultdict(lambda: np.zeros(4)))  # n, kept, lost, wrong
for r in rows:
    b = r["bound"]
    s = site[b]
    s["runs"] += 1
    gtd = r["gt_decision"] in ("pass", "reject")
    s["gt_decided"] += gtd
    s["kept"] += gtd and r["decision"] == r["gt_decision"]
    s["gt_pass"] += r["gt_decision"] == "pass"
    s["pass_kept"] += r["gt_decision"] == "pass" and r["decision"] == "pass"
    s["gt_reject"] += r["gt_decision"] == "reject"
    s["reject_kept"] += r["gt_decision"] == "reject" and r["decision"] == "reject"
    s["unsafe_pass"] += r["decision"] == "pass" and r.get("gt_spot") == "fail"
    s["pass_on_unsure"] += r["decision"] == "pass" and r.get("gt_spot") == "unsure"
    s["wrong_reject"] += r["decision"] == "reject" and r["gt_decision"] != "reject"
    for cid, n in r["n"].items():
        a = chk[cid][b]
        a += [n, r["kept"].get(cid, 0), r["lost"].get(cid, 0), r["wrong"].get(cid, 0)]

out = {"levels": levels, "site": {}, "checks": {}}
L = ["| 95 % scale bound | runs | decided sites kept (pass + reject) | GT passes kept | GT rejects kept | unsafe passes (spot fails in truth) | passes on truth-unsure spots | rejects where truth is not reject |",
     "|---|---|---|---|---|---|---|---|"]
for b in levels:
    s = site[b]
    kept = s["kept"] / max(1, s["gt_decided"])
    out["site"][b] = dict(s) | {"kept_share": kept}
    L.append(f"| {100 * b:.1f} % | {s['runs']} | {s['kept']}/{s['gt_decided']} ({100 * kept:.0f} %) | {s['pass_kept']}/{s['gt_pass']} | "
             f"{s['reject_kept']}/{s['gt_reject']} | {s['unsafe_pass']} | {s['pass_on_unsure']} | {s['wrong_reject']} |")
L += ["", "Per check: share of check instances decided (pass or fail) in truth that stay decided and right; wrong = flipped pass <-> fail.", ""]
ids = sorted(chk, key=lambda c: -chk[c][levels[-1]][0])
pick = [0.0, 0.01, 0.015, 0.02, 0.03, 0.04, 0.06, 0.12, 0.18]
L.append("| check | " + " | ".join(f"{100 * b:.1f} %" for b in pick) + " | wrong at ≤ 2 % / ≤ 6 % |")
L.append("|---|" + "---|" * (len(pick) + 1))
for c in ids:
    cells = []
    for b in pick:
        n, k, lo, w = chk[c][b]
        cells.append(f"{100 * k / max(1, n):.0f}")
    w2 = sum(chk[c][b][3] for b in levels if b <= 0.02) / max(1, sum(chk[c][b][0] for b in levels if b <= 0.02))
    w6 = sum(chk[c][b][3] for b in levels if b <= 0.06) / max(1, sum(chk[c][b][0] for b in levels if b <= 0.06))
    L.append(f"| {c} | " + " | ".join(cells) + f" | {100 * w2:.1f} / {100 * w6:.1f} % |")
    out["checks"][c] = {str(b): chk[c][b].tolist() for b in levels}

# map MC house bounds to the site-kept curve (piecewise linear in bound)
hb = np.load(R / "houses.npz", allow_pickle=True)
names = list(hb["names"])
kept_curve = np.array([out["site"][b]["kept_share"] for b in levels])
pass_curve = np.array([site[b]["pass_kept"] / max(1, site[b]["gt_pass"]) for b in levels])
L += ["", "Monte Carlo houses mapped onto the curves (bound interpolated; each method's bound inflated by the factor k that makes |err| <= k x bound for 95 % of houses):", "",
      "| Method | k | decided sites kept | GT passes kept | houses with bound ≤ 1.5 % / 2 % / 3 % |", "|---|---|---|---|---|"]
out["mapped"] = {}
for i, nm in enumerate(names):
    e0, b0 = np.abs(hb[f"{i}_err"]), np.maximum(hb[f"{i}_bound"], 1e-6)
    kf = max(1.0, float(np.percentile(e0 / b0, 95)))
    b = np.clip(b0 * kf, 0, levels[-1])
    k = float(np.interp(b, levels, kept_curve).mean())
    p = float(np.interp(b, levels, pass_curve).mean())
    out["mapped"][nm] = {"kept": k, "pass_kept": p, "inflation": kf}
    L.append(f"| {nm} | {kf:.2f} | {100 * k:.0f} % | {100 * p:.0f} % | {100 * (b <= 0.015).mean():.0f} / {100 * (b <= 0.02).mean():.0f} / {100 * (b <= 0.03).mean():.0f} % |")
(R / "c1_scale_summary.md").write_text("\n".join(L) + "\n")
json.dump(out, open(R / "c1_scale_summary.json", "w"), indent=1, default=float)
print("\n".join(L))
