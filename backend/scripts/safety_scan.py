"""Refuse a commit that would publish secrets, prompt dumps, or oversized binaries.

Scans files that differ from origin/main, plus untracked files that are not
gitignored. Files already on main, including briefs that name the key pattern, are
left to WS-00's hygiene check. Exit 1 lists every hit.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
SECRET_SCAN_BYTES = 1024 * 1024
DENY_NAMES = {
    "SSR_Prompts.md",
    "ssr-quick-reference-notion.md",
    "meter_numbers_hand.csv",
    "hand_numbers_raw.txt",
}
DENY_SUFFIXES = {".tar", ".7z", ".p8"}
_PRIVATE_MARK = "-----BEGIN " + "PRIVATE KEY"
SECRET = re.compile(rf"sk-[A-Za-z0-9]{{20,}}|xai-[A-Za-z0-9]{{20,}}|AKIA[0-9A-Z]{{16}}|{_PRIVATE_MARK}")


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]).resolve() if argv else Path.cwd()
    hits: list[str] = []
    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        if path.name in DENY_NAMES or path.name == ".env" or path.name.startswith(".env."):
            hits.append(f"denied name: {rel}")
            continue
        if path.suffix.lower() in DENY_SUFFIXES or rel.endswith(".tar.gz"):
            hits.append(f"denied suffix: {rel}")
            continue
        if "private/" in f"{rel}/":
            hits.append(f"private path: {rel}")
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            hits.append(f"unreadable: {rel} ({exc})")
            continue
        if size > MAX_BYTES:
            hits.append(f"over 5 MB ({size} bytes): {rel}")
            continue
        if size > SECRET_SCAN_BYTES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if SECRET.search(text):
            hits.append(f"secret pattern: {rel}")
    if hits:
        print("safety scan failed:", file=sys.stderr)
        for hit in hits:
            print(f"  {hit}", file=sys.stderr)
        return 1
    print(f"safety scan ok ({len(_files(root))} files)")
    return 0


def _files(root: Path) -> list[Path]:
    changed = subprocess.check_output(["git", "-C", str(root), "diff", "--name-only", "-z", "origin/main"], text=True)
    others = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "-z", "--others", "--exclude-standard"], text=True
    )
    names = [n for n in (changed + others).split("\0") if n]
    return [root / name for name in dict.fromkeys(names) if (root / name).is_file()]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
