"""Validate-only entry point. The rest of packetgen stays in the store."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from packetgen.validate import validate_packet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="packetgen")
    sub = parser.add_subparsers(dest="cmd", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("packets", nargs="+")
    validate.add_argument("--no-hashes", action="store_true")
    args = parser.parse_args(argv)
    ok = True
    for manifest in args.packets:
        report = validate_packet(Path(manifest), check_hashes=not args.no_hashes)
        print(json.dumps(report, indent=1))
        ok = ok and bool(report["ok"]) and not report["warnings"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
