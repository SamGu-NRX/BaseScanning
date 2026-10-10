"""Presentation tables and summary, reproducible from the per-case records alone.

Every function here is a pure function of its arguments, so `run.py --replay` recomputes
committed results from the raw per-case reader output without running any reader, and
test_replay checks the recomputation is byte-identical.
"""

import csv
import io
import json

from meter_candidates.scoring import aggregate

# Constant, not measured: Apple Vision through meterocr needs macOS and Swift; this Linux
# sandbox cannot run it, and a mock is not OCR evidence, so no substitute arm ran.
METEROCR_NOT_RUN = (
    "Apple Vision through meterocr needs macOS; this Linux sandbox cannot run it, and a "
    "mock is not OCR evidence, so no substitute arm ran"
)


def cases_csv(records: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(
        ["id", "arm", "present", "outcome", "rank", "candidates_total", "candidates_offered"]
    )
    for r in records:
        writer.writerow(
            [
                r["id"],
                r["arm"],
                int(r["present"]),
                r["outcome"],
                r["rank"],
                r["candidates_total"],
                r["candidates_offered"],
            ]
        )
    return buffer.getvalue()


def build_summary(records: list[dict], arms_meta: dict) -> dict:
    """Arm statuses plus outcome counts. arms_meta is the manifest's 'arms' object."""
    seen: dict[str, dict] = {}
    for arm in sorted(arms_meta):
        seen[arm] = {"status": "not-run", "note": arms_meta[arm].get("note", "")}
    for r in records:
        entry = seen.setdefault(r["arm"], {"status": "ran"})
        entry["status"] = "ran"
        entry["cases"] = sum(row["arm"] == r["arm"] for row in records)
        version = (r.get("reader") or {}).get("version")
        if version:
            entry.setdefault("version", version)
    return {"arms": seen, "counts": aggregate(records)}


def tables_md(summary: dict, records: list[dict]) -> str:
    lines = ["# Meter candidate selection — results", ""]
    lines.append(
        "Cases scored against gold, per reader arm. Causes of missed offers split into "
        "ranking (the serial's core was a candidate) and recognition (it never was)."
    )
    lines += ["", "## Outcome counts", ""]
    header = (
        "| arm | cases | top-1 | top-3 | rank miss | filtered miss | recognition miss "
        "| correct rejection | false offer |"
    )
    lines += [header, "|" + "---|" * 9]
    for arm, counts in summary["counts"].items():
        lines.append(
            f"| {arm} | {counts['cases']} | {counts['top1']} | {counts['top3']} "
            f"| {counts['rank_miss']} | {counts['filtered_miss']} | {counts['recognition_miss']} "
            f"| {counts['correct_rejection']} | {counts['false_offer']} |"
        )
    lines += ["", "## Causes of missed offers (serial present, not offered top-1)", ""]
    lines += ["| arm | ranking cause | recognition cause |", "|---|---|---|"]
    for arm, counts in summary["counts"].items():
        lines.append(
            f"| {arm} | {counts['ranking_cause']} | {counts['recognition_cause']} |"
        )
    lines += ["", "## Arm status", ""]
    for arm, meta in summary["arms"].items():
        note = f" — {meta['note']}" if meta.get("note") else ""
        version = f", {meta['version']}" if meta.get("version") else ""
        ran = f", {meta['cases']} cases" if meta.get("cases") is not None else ""
        lines.append(f"- **{arm}**: {meta['status']}{ran}{version}{note}")
    lines += ["", "## Per case", ""]
    lines += [
        "| id | arm | present | outcome | rank | candidates | offered |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in records:
        lines.append(
            f"| {r['id']} | {r['arm']} | {int(r['present'])} | {r['outcome']} | {r['rank']} "
            f"| {r['candidates_total']} | {r['candidates_offered']} |"
        )
    lines.append("")
    return "\n".join(lines)


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"
