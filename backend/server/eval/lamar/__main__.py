"""``python -m server.eval.lamar run``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from server.eval.lamar import available


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="server.eval.lamar")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--segment", default=None)
    run.add_argument("--data", type=Path, default=Path.home() / "lamar")
    run.add_argument("--out", type=Path, default=Path("runs/lamar"))
    args = ap.parse_args(argv)
    if args.cmd == "run":
        note = available(args.data)
        if note:
            print(note)
            return 0
        print("LaMAR segments would run here; the data check passed but this PR does not launch SfM", file=sys.stderr)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
