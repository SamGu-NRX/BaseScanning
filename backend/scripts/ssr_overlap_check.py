"""Fail if a tracked text file shares a 12-word shingle with the SSR prompts.

Shingles are SHA-256 hashes of normalized words (lowercase alphanumeric tokens,
space-joined). The hash list is data/hygiene/ssr_shingles.sha256. It contains
hashes only. WS-00 should wire this script into `make hygiene`.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

WORD_RE = re.compile(r"[A-Za-z0-9]+")
SHINGLE_LEN = 12
MAX_BYTES = 5 * 1024 * 1024
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".pdf",
    ".ply",
    ".bin",
    ".usdz",
    ".obj",
    ".mtl",
    ".zip",
    ".7z",
    ".gz",
    ".pyc",
    ".woff",
    ".woff2",
    ".ico",
    ".mp4",
    ".mov",
    ".heic",
}


def repo_root() -> Path:
    """The package root, two levels above this script.

    Before the port into the BaseScanning monorepo the git toplevel held
    data/hygiene; the package now lives at backend/ inside the monorepo, so
    the toplevel no longer contains data/hygiene/ssr_shingles.sha256.
    """
    return Path(__file__).resolve().parent.parent


def normalized_words(text: str) -> list[str]:
    return [match.group(0).lower() for match in WORD_RE.finditer(text)]


def shingle_digest(words: list[str]) -> str:
    if len(words) != SHINGLE_LEN:
        raise ValueError(f"expected {SHINGLE_LEN} words, got {len(words)}")
    return hashlib.sha256(" ".join(words).encode()).hexdigest()


def shingle_digests(text: str) -> set[str]:
    words = normalized_words(text)
    return {
        shingle_digest(words[index : index + SHINGLE_LEN]) for index in range(0, max(0, len(words) - SHINGLE_LEN + 1))
    }


def load_digests(path: Path) -> set[str]:
    digests: set[str] = set()
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not DIGEST_RE.fullmatch(line):
            raise SystemExit(f"bad shingle digest at {path}:{line_number}")
        digests.add(line)
    if not digests:
        raise SystemExit(f"no shingle digests in {path}")
    return digests


def overlapping_shingle_count(text: str, digests: set[str]) -> int:
    words = normalized_words(text)
    count = 0
    for index in range(0, max(0, len(words) - SHINGLE_LEN + 1)):
        if shingle_digest(words[index : index + SHINGLE_LEN]) in digests:
            count += 1
    return count


def iter_tracked_text(root: Path):
    listed = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
    for rel in listed.split(b"\0"):
        if not rel:
            continue
        path = root / rel.decode()
        if not path.is_file() or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        size = path.stat().st_size
        if size == 0 or size > MAX_BYTES:
            continue
        blob = path.read_bytes()
        if b"\0" in blob[:8192]:
            continue
        yield path, blob.decode("utf-8", errors="ignore")


def find_overlaps(root: Path, digests: set[str]) -> dict[str, int]:
    hits: dict[str, int] = {}
    for path, text in iter_tracked_text(root):
        count = overlapping_shingle_count(text, digests)
        if count:
            hits[path.relative_to(root).as_posix()] = count
    return hits


def emit_shingles(source: Path, dest: Path) -> int:
    digests = sorted(shingle_digests(source.read_text(encoding="utf-8", errors="ignore")))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(f"{digest}\n" for digest in digests))
    return len(digests)


def main(argv: list[str] | None = None) -> int:
    root = repo_root()
    parser = argparse.ArgumentParser(description="Detect 12-word shingle overlap with the SSR prompts.")
    parser.add_argument("--root", type=Path, default=root)
    parser.add_argument("--shingles", type=Path, default=root / "data/hygiene/ssr_shingles.sha256")
    parser.add_argument("--emit-shingles", type=Path, help="Read a private prompt source and write hashes only.")
    parser.add_argument("--out", type=Path, help="Destination for --emit-shingles.")
    args = parser.parse_args(argv)

    if args.emit_shingles:
        if args.out is None:
            raise SystemExit("--emit-shingles requires --out")
        count = emit_shingles(args.emit_shingles, args.out)
        print(f"wrote {count} shingle digests")
        return 0

    digests = load_digests(args.shingles)
    hits = find_overlaps(args.root, digests)
    if not hits:
        print("ssr overlap: 0")
        return 0
    shingle_hits = sum(hits.values())
    print(f"ssr overlap: {len(hits)} files, {shingle_hits} shingles")
    for rel, count in sorted(hits.items()):
        print(f"{rel} count={count}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
