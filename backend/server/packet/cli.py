"""CPU CLI for validate, normalize, capture-check, corrupt and schema export."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal

from server.packet.capture_check import CaptureCheck, capture_check
from server.packet.normalize import PacketDerived, load_normalized, normalize_packet
from server.packet.report import PacketError
from server.packet.validate import validate_packet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m server.packet.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("manifests", nargs="+", type=Path)
    validate.add_argument("--json", type=Path)
    validate.add_argument("--no-hashes", action="store_true")
    validate.add_argument("--manifest-only", action="store_true")

    normalize = sub.add_parser("normalize")
    normalize.add_argument("manifest", type=Path)
    normalize.add_argument("--flow", choices=("guided", "photo"))
    normalize.add_argument("--out", type=Path, required=True)

    check = sub.add_parser("capture-check")
    check.add_argument("normalized", type=Path)
    check.add_argument("--mode", choices=("partial", "final"), default="final")

    corrupt = sub.add_parser("corrupt")
    corrupt.add_argument("manifest", type=Path)
    corrupt.add_argument("--case", required=True)
    corrupt.add_argument("--out", type=Path, required=True)

    schemas = sub.add_parser("export-schemas")
    schemas.add_argument("--check", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "validate":
            return _validate(args)
        if args.cmd == "normalize":
            return _normalize(args)
        if args.cmd == "capture-check":
            return _check(args)
        if args.cmd == "corrupt":
            return _corrupt(args)
        if args.cmd == "export-schemas":
            return _schemas(args.check)
    except PacketError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    except Exception:
        print("schema", file=sys.stderr)
        return 2
    return 2


def _validate(args) -> int:
    reports = []
    worst = 0
    stage: Literal["manifest", "full"] = "manifest" if args.manifest_only else "full"
    for manifest in args.manifests:
        if not manifest.is_file():
            print(f"missing {manifest}", file=sys.stderr)
            return 2
        report = validate_packet(manifest, stage=stage, check_hashes=not args.no_hashes)
        payload = report.model_dump(mode="json")
        reports.append(payload)
        print(json.dumps(payload))
        if not report.ok:
            worst = 1
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    return worst


def _normalize(args) -> int:
    report = validate_packet(args.manifest, stage="full")
    packet = normalize_packet(args.manifest, flow=args.flow, report=report)
    check = capture_check(packet, mode="final", report_ok=report.ok)
    packet.derived.captureCheck = check.model_dump(mode="json")
    args.out.mkdir(parents=True, exist_ok=True)
    packet.to_json(args.out / "normalized.json")
    (args.out / "validation.json").write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    (args.out / "capture_check.json").write_text(
        json.dumps(check.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    return 0 if report.ok else 1


def _check(args) -> int:
    packet = load_normalized(args.normalized, args.normalized.parent)
    check = capture_check(packet, mode=args.mode, report_ok=packet.derived.validation.ok)
    print(json.dumps(check.model_dump(mode="json"), indent=2))
    return 0 if check.complete or args.mode == "partial" else 1


def _corrupt(args) -> int:
    from server.packet.tests.corrupt import corrupt_case

    corrupt_case(args.manifest, args.case, args.out)
    return 0


def _schemas(check: bool) -> int:
    directory = Path(__file__).resolve().parent / "schemas"
    directory.mkdir(parents=True, exist_ok=True)
    derived = PacketDerived.model_json_schema()
    defs = derived.pop("$defs", {})
    normalized = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "normalized-packet.v0.schema.json",
        "$defs": defs,
        "type": "object",
        "required": ["ext"],
        "properties": {
            "ext": {
                "type": "object",
                "required": ["hss"],
                "properties": {"hss": derived},
            }
        },
    }
    capture = CaptureCheck.model_json_schema()
    capture["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    capture["$id"] = "capture-check.v0.schema.json"
    files = {
        "normalized-packet.v0.schema.json": normalized,
        "capture-check.v0.schema.json": capture,
    }
    drifted = False
    for name, payload in files.items():
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        path = directory / name
        if check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                print(f"drift {path}", file=sys.stderr)
                drifted = True
        else:
            path.write_text(text, encoding="utf-8")
    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())
