#!/usr/bin/env python3
"""Fail when a tracked file is too large, on the deny-list, or contains a secret."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 5_000_000
DENY_NAMES = {
    "SSR_Prompts.md",
    "ssr-quick-reference-notion.md",
    "meter_numbers_hand.csv",
    "hand_numbers_raw.txt",
}
SECRET = re.compile(r"(?:sk-[A-Za-z0-9]{20,}|xai-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".jsonl", ".csv", ".yaml", ".yml", ".toml", ".sh"}


def denied(rel: str) -> str | None:
    name = Path(rel).name
    if name in DENY_NAMES:
        return name
    if name == ".env" or name.startswith(".env"):
        return "env file"
    if name.endswith((".p8", ".7z", ".tar")) or ".tar.part-" in name:
        return name
    return None


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    problems: list[str] = []
    for raw in listed:
        if not raw:
            continue
        rel = raw.decode()
        path = root / rel
        if not path.is_file():
            continue
        why = denied(rel)
        if why:
            problems.append(f"{rel}: deny-list ({why})")
            continue
        size = path.stat().st_size
        if size > MAX_BYTES:
            problems.append(f"{rel}: {size} bytes exceeds {MAX_BYTES}")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(errors="ignore")
        if SECRET.search(text):
            problems.append(f"{rel}: secret pattern")
    if problems:
        print("hygiene failed:", file=sys.stderr)
        for item in problems:
            print(item, file=sys.stderr)
        return 1
    print(f"hygiene ok ({len([raw for raw in listed if raw])} tracked files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
