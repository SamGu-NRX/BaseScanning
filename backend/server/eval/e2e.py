"""``make e2e`` entry. Exits 2 when it does not replay.

A missing API is not a PASS. A URL that this process does not upload to is not a PASS
either: the nightly job must fail until a replay actually runs.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from server.eval.gates import evaluate
from server.eval.report import markdown, write


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="server.eval.e2e")
    ap.add_argument(
        "--local", action="store_true", help="in-process API (WS-01); not available until that package imports"
    )
    ap.add_argument("--config", type=Path, default=Path("server/eval/gates.yaml"))
    ap.add_argument("--inputs", type=Path, default=Path("data/fixtures"))
    ap.add_argument("--out", type=Path, default=Path("runs/e2e/latest"))
    args = ap.parse_args(argv)
    url = os.environ.get("CAPTURE_API_URL", "")
    if args.local or not url:
        print(
            "SKIP e2e: CAPTURE_API_URL is unset and the in-process API is not importable. "
            "Pipeline rows stay SKIP; this is not a pass.",
            file=sys.stderr,
        )
        report = evaluate(args.config, args.inputs)
        write(report, args.out)
        sys.stdout.write(markdown(report))
        return 2
    print(
        f"SKIP live upload to {url}: no packet was replayed. "
        "This is not a pass.",
        file=sys.stderr,
    )
    report = evaluate(args.config, args.inputs)
    write(report, args.out)
    sys.stdout.write(markdown(report))
    return 2


if __name__ == "__main__":
    sys.exit(main())
