"""Markdown cost table from a bench jsonl, a log jsonl, or a replay file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from server.bench import estimate_usd


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def gpu_usd(path: Path) -> float:
    """Return est_usd from the first bench record, else the rate-card estimate."""
    rows = _load_jsonl(path)
    if not rows:
        return 0.0
    record = rows[0]
    if record.get("est_usd") is not None:
        return float(record["est_usd"])
    seconds = float(record.get("client_s") or record.get("method_s") or 0)
    return estimate_usd(str(record.get("gpu") or "L4"), seconds)


def _table(rows: list[dict]) -> str:
    header = "| house | gpu | gpu_s | gpu_usd | vlm_usd | total_usd | time_to_verdict_s | disagreements |"
    rule = "|---|---|---:|---:|---:|---:|---:|---:|"
    lines = [header, rule]
    for record in rows:
        seconds = float(record.get("client_s") or record.get("method_s") or record.get("gpu_s") or 0)
        gpu = str(record.get("gpu") or "")
        stated = record.get("est_usd")
        gpu_cost = float(stated) if stated is not None else (estimate_usd(gpu, seconds) if gpu else 0.0)
        vlm = float(record.get("vlm_usd") or 0)
        total = float(record.get("total_usd") or (gpu_cost + vlm))
        verdict_s = record.get("time_to_verdict_s", "")
        disagreements = record.get("disagreements", "")
        house = record.get("house") or record.get("capture_id") or ""
        lines.append(
            f"| {house} | {gpu} | {seconds:.3f} | {gpu_cost:.4f} | {vlm:.4f} | {total:.4f} | {verdict_s} | {disagreements} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print a per-house cost table.")
    parser.add_argument("--bench", type=Path)
    parser.add_argument("--logs", type=Path)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--cost", type=Path)
    args = parser.parse_args(argv)
    rows: list[dict] = []
    if args.bench:
        rows.extend(_load_jsonl(args.bench))
    if args.logs:
        rows.extend(_load_jsonl(args.logs))
    if args.replay:
        payload = json.loads(args.replay.read_text())
        if isinstance(payload, dict):
            rows.append(payload)
        else:
            rows.extend(payload)
    if args.cost:
        extra = json.loads(args.cost.read_text())
        if rows:
            rows[-1].update(extra if isinstance(extra, dict) else {})
        elif isinstance(extra, dict):
            rows.append(extra)
    if not rows:
        print("no rows", file=sys.stderr)
        return 1
    print(_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
