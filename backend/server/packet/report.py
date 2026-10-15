"""Validation report types from WS-00, plus the stage error and issue bag."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from server.contracts.validation import ValidationIssue, ValidationReport


class PacketError(Exception):
    """Raised by the stage entry when the packet cannot be used."""

    def __init__(self, code: str, report: ValidationReport | None = None, message: str = "") -> None:
        if code not in {"invalid", "root_missing", "io"}:
            raise ValueError(code)
        self.code = code
        self.report = report
        super().__init__(message or code)


@dataclass
class Bag:
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    def add(self, level: Literal["error", "warning"], code: str, pointer: str, message: str, value: Any = None) -> None:
        issue = ValidationIssue(code=code, level=level, pointer=pointer, message=message, value=value)
        if level == "error":
            self.errors.append(issue)
        else:
            self.warnings.append(issue)

    def error(self, code: str, pointer: str, message: str, value: Any = None) -> None:
        self.add("error", code, pointer, message, value)

    def warn(self, code: str, pointer: str, message: str, value: Any = None) -> None:
        self.add("warning", code, pointer, message, value)

    @property
    def ok(self) -> bool:
        return not self.errors


def pointer(*parts: object) -> str:
    bits: list[str] = []
    for part in parts:
        text = str(part).replace("~", "~0").replace("/", "~1")
        bits.append(text)
    return "/" + "/".join(bits) if bits else ""


def log_issues(report: ValidationReport, capture_id: str | None) -> None:
    """One JSON line per issue. Codes and pointers only; messages can hold rawText."""
    for issue in list(report.errors) + list(report.warnings):
        line = {
            "level": "error" if issue.level == "error" else "warning",
            "event": "packet_issue",
            "msg": issue.code,
            "code": issue.code,
            "pointer": issue.pointer,
            "capture_id": capture_id,
        }
        print(json.dumps(line, separators=(",", ":")), flush=True)
