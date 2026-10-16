"""Summarise outputs/sweep/results.jsonl: per error level and mode, decision flips vs the
ground-truth verdict of the same scene x profile, unsafe passes (perturbed pass whose spot is not
a PASS in the ground truth), and pass / manual-review rates. Writes outputs/sweep/summary.md."""

import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
rows = [json.loads(x) for x in open(HERE / "outputs/sweep/results.jsonl")]
gt = {(r["scene"], r["profile"]): r for r in rows if r["level"] == "gt"}
order = []
for r in rows:
    if r["level"] != "gt" and r["level"] not in order:
        order.append(r["level"])
agg = collections.defaultdict(lambda: collections.Counter())
by_prof = collections.defaultdict(lambda: collections.Counter())
for r in rows:
    if r["level"] == "gt":
        continue
    k = (r["level"], r["mode"])
    c = agg[k]
    c["n"] += 1
    if r["decision"] == "error":
        c["error"] += 1
        continue
    g = gt[(r["scene"], r["profile"])]
    c["flip"] += r["decision"] != g["decision"]
    c["pass"] += r["decision"] == "pass"
    c["review"] += r["decision"] == "manual_review"
    c["reject"] += r["decision"] == "reject"
    unsafe = r["decision"] == "pass" and r.get("gt_spot", {}).get("outcome") != "pass"
    c["unsafe"] += unsafe
    c["gt_pass"] += g["decision"] == "pass"
    c["lost_pass"] += g["decision"] == "pass" and r["decision"] != "pass"
    c["false_reject"] += r["decision"] == "reject" and g["decision"] != "reject"
    bp = by_prof[(r["level"], r["mode"], r["profile"])]
    bp["n"] += 1
    bp["unsafe"] += unsafe
    bp["flip"] += r["decision"] != g["decision"]

lines = ["# C1 error sweep summary", "",
         f"{len(rows)} runs. Ground truth per scene x profile: " +
         ", ".join(f"{s}/{p}={g['decision']}" for (s, p), g in sorted(gt.items())), "",
         "| level | mode | runs | flips | unsafe pass | pass | manual_review | reject (GT not reject) | lost GT pass | errors |",
         "|---|---|---|---|---|---|---|---|---|---|"]
for lv in order:
    for mode in ("informed", "naive"):
        c = agg.get((lv, mode))
        if not c:
            continue
        n = c["n"] - c["error"]
        pct = lambda x: f"{100 * x / n:.0f}%" if n else "-"  # noqa: E731
        lines.append(f"| {lv} | {mode} | {c['n']} | {pct(c['flip'])} | {c['unsafe']} ({pct(c['unsafe'])}) | {pct(c['pass'])} | "
                     f"{pct(c['review'])} | {c['false_reject']} | {c['lost_pass']}/{c['gt_pass']} | {c['error']} |")
lines += ["", "Unsafe passes by profile (level, mode, profile: unsafe/runs):", ""]
for (lv, mode, p), c in sorted(by_prof.items(), key=lambda kv: order.index(kv[0][0])):
    if c["unsafe"]:
        lines.append(f"- {lv} {mode} {p}: {c['unsafe']}/{c['n']}")
(HERE / "outputs/sweep/summary.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
