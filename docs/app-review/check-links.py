#!/usr/bin/env python3
"""Check that every relative link and heading anchor in docs/app-review resolves.

Run from the repository root with: python3 docs/app-review/check-links.py

A link passes when its target file exists and, for links that end in an
#anchor, when the anchor names a heading in the target file, using GitHub's
slug style. Links to websites are skipped. Exits 0 when everything resolves,
prints each broken link and exits 1 otherwise.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
HEADING = re.compile(r"#{1,6}\s+(.*)")
SKIP_PREFIXES = ("http://", "https://", "mailto:")


def slug(heading_text: str) -> str:
    text = heading_text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text)


def heading_slugs(path: Path) -> set:
    slugs = set()
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        match = HEADING.match(line)
        if not in_fence and match:
            slugs.add(slug(match.group(1)))
    return slugs


def links_outside_fences(path: Path) -> list:
    text = path.read_text(encoding="utf-8")
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    return LINK.findall(prose)


def main() -> int:
    files = {path: heading_slugs(path) for path in sorted(ROOT.rglob("*.md"))}
    broken = []
    checked = 0
    for path, slugs in files.items():
        for target in links_outside_fences(path):
            if target.startswith(SKIP_PREFIXES):
                continue
            checked += 1
            file_part, _, anchor = target.partition("#")
            resolved = (path.parent / file_part).resolve() if file_part else path
            if not resolved.is_file():
                broken.append(f"{path.relative_to(ROOT)}: {target} (missing file)")
            elif anchor and resolved.suffix == ".md":
                targets = files.get(resolved) or heading_slugs(resolved)
                if anchor not in targets:
                    broken.append(f"{path.relative_to(ROOT)}: {target} (missing anchor)")
    if broken:
        print(f"{len(broken)} of {checked} relative links broken:")
        for entry in broken:
            print(f"  {entry}")
        return 1
    print(f"OK: all {checked} relative links and anchors in docs/app-review resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
