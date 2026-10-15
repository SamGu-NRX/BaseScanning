"""Markdown and JSON for an hss-gates/v0 table."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GateReport:
    stamp: str
    git_sha: str
    rows: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    format: str = "hss-gates/v0"

    def to_dict(self) -> dict:
        return {
            "format": self.format,
            "stamp": self.stamp,
            "git_sha": self.git_sha,
            "rows": self.rows,
            "summary": self.summary,
        }


def markdown(report: GateReport) -> str:
    lines = [
        f"# Gate table {report.stamp}",
        "",
        f"git `{report.git_sha}`",
        "",
        "| id | group | target | metric | value | threshold | status | evidence | note |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report.rows:
        cells = [
            row.get("id", ""),
            row.get("group", ""),
            row.get("target", ""),
            row.get("metric", ""),
            _cell(row.get("value")),
            _cell(row.get("threshold")),
            row.get("status", ""),
            _cell(row.get("evidence_path")),
            _cell(row.get("note")),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    summary = report.summary
    lines += [
        "",
        f"summary: pass {summary.get('pass', 0)}, fail {summary.get('fail', 0)}, "
        f"known_fail {summary.get('known_fail', 0)}, "
        f"warn {summary.get('warn', 0)}, skip {summary.get('skip', 0)}",
    ]
    return "\n".join(lines) + "\n"


def write(report: GateReport, out: Path) -> tuple[Path, Path]:
    """Write ``gates.json`` and ``gates.md``. ``out`` may be either file or a directory."""
    if out.suffix == ".json":
        json_path, md_path = out, out.with_suffix(".md")
    elif out.suffix == ".md":
        md_path, json_path = out, out.with_suffix(".json")
    else:
        out.mkdir(parents=True, exist_ok=True)
        json_path, md_path = out / "gates.json", out / "gates.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report.to_dict(), indent=2) + "\n")
    md_path.write_text(markdown(report))
    return json_path, md_path


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "/").replace("\n", " ")
