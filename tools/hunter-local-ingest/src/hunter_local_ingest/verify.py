"""hunter-verify-run: re-check a finished run directory on its own.

It does not trust the run's own verdicts about its receipts. It replays the
integrity chain, re-validates every artifact against the export schema
(the paths and packet ids it may contain come from the manifest's fixture,
which must itself match the generator's patterns), re-runs the privacy
screen over the files as they are now, and checks the recorded screen said
clean. Output names categories and files, never content.

Like the chain itself, this detects edits; it cannot prove the owner of
the directory did not rewrite everything consistently.

Exit 0 when nothing was found, 5 otherwise, 2 for a usage error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .export import (
    ExportContext,
    ExportError,
    validate_check_line,
    validate_manifest,
    validate_screen,
    validate_summary,
)
from .receipts import (
    CHECKS,
    MANIFEST,
    SCREEN_FILE,
    SUMMARY,
    context_for,
    read_json,
    verify_run_integrity,
)
from .runner import FLAG_KINDS
from .sanitize import screen_artifact


def _context(manifest: object) -> ExportContext | None:
    """The paths and packet ids the manifest's own fixture declares, if well formed."""
    if not isinstance(manifest, dict):
        return None
    fixture = manifest.get("fixture")
    run_id = manifest.get("run_id")
    if not isinstance(fixture, dict) or not isinstance(run_id, str):
        return None
    files = fixture.get("files")
    packet_id = fixture.get("packet_id")
    if not isinstance(files, list) or not isinstance(packet_id, str):
        return None
    paths = [f.get("path") for f in files if isinstance(f, dict)]
    if len(paths) != len(files) or not all(isinstance(path, str) for path in paths):
        return None
    try:
        return context_for(frozenset(paths), packet_id, run_id[:8])
    except ExportError:
        return None


def _schema(problems: list[str], name: str, check) -> None:
    try:
        check()
    except ExportError as exc:
        problems.append(f"{name}: schema {exc.category}")
    except (KeyError, TypeError, AttributeError):
        problems.append(f"{name}: schema malformed")


def verify_run(run_dir: Path) -> list[str]:
    problems = list(verify_run_integrity(run_dir))
    manifest, problem = read_json(run_dir / MANIFEST)
    ctx = None if problem else _context(manifest)
    if problem:
        problems.append(f"{MANIFEST}: {problem}")
    elif ctx is None:
        problems.append(f"{MANIFEST}: fixture is not in the expected format")
    if ctx is not None:
        _schema(problems, MANIFEST, lambda: validate_manifest(manifest, ctx, FLAG_KINDS))
        lines, problem = read_json(run_dir / CHECKS, lines=True)
        if problem:
            problems.append(f"{CHECKS}: {problem}")
        else:
            for lineno, line in enumerate(lines, 1):
                _schema(
                    problems, f"{CHECKS}:{lineno}", lambda line=line: validate_check_line(line, ctx)
                )
        summary, problem = read_json(run_dir / SUMMARY)
        if problem:
            problems.append(f"{SUMMARY}: {problem}")
        else:
            _schema(problems, SUMMARY, lambda: validate_summary(summary, ctx))
        screen, problem = read_json(run_dir / SCREEN_FILE)
        if problem:
            problems.append(f"{SCREEN_FILE}: {problem}")
        else:
            _schema(problems, SCREEN_FILE, lambda: validate_screen(screen, ctx))
            if not isinstance(screen, dict) or screen.get("clean") is not True:
                problems.append(f"{SCREEN_FILE}: recorded findings")
    for name in (MANIFEST, CHECKS, SUMMARY):
        path = run_dir / name
        try:
            findings = screen_artifact(str(path)) if path.exists() else []
        except OSError:
            findings = [f"{name}: unreadable"]
        problems.extend(f"rescreen {finding}" for finding in findings)
    # one problem may be reached two ways; report it once, in order
    return list(dict.fromkeys(problems))


def main() -> int:
    parser = argparse.ArgumentParser(prog="hunter-verify-run", description=__doc__)
    parser.add_argument("run_dir")
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print("hunter-verify-run: error: not a directory", file=sys.stderr)
        return 2
    problems = verify_run(run_dir)
    for problem in problems:
        print(problem)
    print("verify: OK" if not problems else f"verify: {len(problems)} problem(s)")
    return 0 if not problems else 5


if __name__ == "__main__":
    raise SystemExit(main())
