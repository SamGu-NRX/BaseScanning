"""Kit commands. Run from ``ios-kit`` so ``packetgen`` and ``ios_kit`` import."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ios_kit.checks import check_ar, check_prompts, check_size, check_spec
from ios_kit.minify import minify
from ios_kit.replay import replay_cli
from ios_kit.schemas import validate_packet_schema_only
from ios_kit.vendored import validate_packet


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            "usage: python -m ios_kit replay|validate|minify|check-spec|check-prompts|check-ar|check-size",
            file=sys.stderr,
        )
        return 2
    command, rest = args[0], args[1:]
    if command == "replay":
        return replay_cli(rest)
    if command == "validate":
        return _validate(rest)
    if command == "minify":
        return _minify(rest)
    if command == "check-spec":
        parser = argparse.ArgumentParser(prog="ios_kit check-spec")
        parser.add_argument("spec", type=Path)
        return check_spec(parser.parse_args(rest).spec)
    if command == "check-prompts":
        return check_prompts()
    if command == "check-ar":
        parser = argparse.ArgumentParser(prog="ios_kit check-ar")
        parser.add_argument("result", type=Path)
        return check_ar(parser.parse_args(rest).result)
    if command == "check-size":
        return check_size()
    print(f"unknown command {command}", file=sys.stderr)
    return 2


def _validate(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ios_kit validate")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument("--no-hashes", action="store_true")
    args = parser.parse_args(argv)
    ok = True
    for path in args.paths:
        if args.schema_only:
            document = json.loads(path.read_text())
            errors = validate_packet_schema_only(document)
            print(json.dumps({"packet": str(path), "ok": not errors, "errors": errors}, indent=1))
            ok = ok and not errors
            continue
        report = validate_packet(path, check_hashes=not args.no_hashes)
        print(json.dumps(report, indent=1))
        ok = ok and bool(report["ok"]) and not report["warnings"]
    return 0 if ok else 1


def _minify(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ios_kit minify")
    parser.add_argument("--from", dest="source", type=Path, required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--keyframes", default=None)
    parser.add_argument("--stills", default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    packet = minify(args.source, args.variant, args.out, keyframes=args.keyframes, stills=args.stills)
    report = validate_packet(args.out / "packet.json", check_hashes=True)
    if not report["ok"] or report["warnings"]:
        print(json.dumps({"errors": report["errors"], "warnings": report["warnings"]}, indent=1), file=sys.stderr)
        return 1
    print(packet["packetId"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
