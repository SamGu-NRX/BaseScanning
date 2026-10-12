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
import json
import sys
from pathlib import Path

from .export import (
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
    verify_run_integrity,
)
from .runner import FLAG_KINDS
from .sanitize import screen_artifact


def verify_run(run_dir: Path) -> list[str]:
    problems = list(verify_run_integrity(run_dir))
    try:
        manifest = json.loads((run_dir / MANIFEST).read_text(encoding="utf-8"))
        fixture = manifest["fixture"]
        ctx = context_for(frozenset(f["path"] for f in fixture["files"]), fixture["packet_id"])
        validate_manifest(manifest, ctx, FLAG_KINDS)
        for lineno, line in enumerate(
            (run_dir / CHECKS).read_text(encoding="utf-8").splitlines(), 1
        ):
            try:
                validate_check_line(json.loads(line), ctx)
            except (json.JSONDecodeError, ExportError) as exc:
                category = exc.category if isinstance(exc, ExportError) else "not-json"
                problems.append(f"{CHECKS}:{lineno}: schema {category}")
        validate_summary(json.loads((run_dir / SUMMARY).read_text(encoding="utf-8")), ctx)
        screen = json.loads((run_dir / SCREEN_FILE).read_text(encoding="utf-8"))
        validate_screen(screen, ctx)
        if screen.get("clean") is not True:
            problems.append(f"{SCREEN_FILE}: recorded findings")
    except FileNotFoundError:
        problems.append("run-dir: cannot validate with a missing artifact")
    except (json.JSONDecodeError, KeyError, TypeError):
        problems.append("run-dir: an artifact is not in the expected format")
    except ExportError as exc:
        problems.append(f"{exc.artifact}{exc.pointer}: schema {exc.category}")
    for name in (MANIFEST, CHECKS, SUMMARY):
        if (run_dir / name).exists():
            problems.extend(
                f"rescreen {finding}" for finding in screen_artifact(str(run_dir / name))
            )
    return problems


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
