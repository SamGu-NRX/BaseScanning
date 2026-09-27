"""`python -m server.contracts.cli check|check-packets`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from server.contracts._base import dump_json
from server.contracts.labels import Labels, labels_consistency_errors
from server.contracts.packet import Packet
from server.contracts.scene import Scene, scene_consistency_errors
from server.contracts.validation import check_packet
from server.contracts.verdict import Verdict, verdict_contract_errors

# server.eval is vendored by the skeleton PR. Until that import exists, checks
# use the contract validators above. Not a circular import: the package is optional.
try:
    from server.eval.sceneio import scene_consistency_errors as _sceneio_scene_errors
    from server.eval.sceneio import schema_errors as _sceneio_schema_errors
    from server.eval.sceneio import verdict_contract_errors as _sceneio_verdict_errors
except ImportError:  # pragma: no cover - skeleton PR adds server.eval
    _sceneio_schema_errors = None
    _sceneio_scene_errors = None
    _sceneio_verdict_errors = None

_FORMATS = {
    "house-capture-packet": Packet,
    "base-scene/v0": Scene,
    "base-verdict/v0": Verdict,
    "base-labels/v0": Labels,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m server.contracts.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate documents by their format field")
    check.add_argument("files", nargs="+", type=Path)
    packets = sub.add_parser("check-packets", help="manifest checks, optionally against files on disk")
    packets.add_argument("paths", nargs="+", type=Path)
    packets.add_argument("--files", action="store_true", help="hash files next to each manifest")
    packets.add_argument("--no-files", action="store_true", help="manifest checks only")
    args = parser.parse_args(argv)
    if args.command == "check":
        return _check(args.files)
    root_mode = bool(args.files) and not args.no_files
    return _check_packets(args.paths, with_files=root_mode)


def _check(paths: list[Path]) -> int:
    failed = False
    for path in paths:
        document = json.loads(path.read_text())
        errors = _document_errors(document)
        if errors:
            failed = True
            print(f"FAIL {path}")
            for error in errors:
                print(f"  {error}")
        else:
            print(f"ok   {path}")
    return 1 if failed else 0


def _document_errors(document: dict) -> list[str]:
    fmt = document.get("format")
    model = _FORMATS.get(fmt)
    if model is None:
        return [f"unknown format {fmt!r}"]
    try:
        parsed = model.model_validate(document)
    except ValidationError as exc:
        return [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors()]
    if fmt == "house-capture-packet":
        report = check_packet(parsed)
        return [f"{item.pointer}: {item.message}" for item in report.errors]
    dumped = dump_json(parsed)
    extra: list[str] = []
    if fmt == "base-scene/v0":
        extra = scene_consistency_errors(dumped)
    elif fmt == "base-verdict/v0":
        extra = verdict_contract_errors(dumped)
    elif fmt == "base-labels/v0":
        extra = labels_consistency_errors(dumped)
    extra.extend(_sceneio_errors(document))
    return extra


def _sceneio_errors(document: dict) -> list[str]:
    if _sceneio_schema_errors is None or _sceneio_scene_errors is None or _sceneio_verdict_errors is None:
        return []
    errors = _sceneio_schema_errors(document)
    if document.get("format") == "base-scene/v0":
        errors.extend(_sceneio_scene_errors(document))
    if document.get("format") == "base-verdict/v0":
        errors.extend(_sceneio_verdict_errors(document))
    return errors


def _check_packets(paths: list[Path], *, with_files: bool) -> int:
    failed = False
    for path in _expand(paths):
        document = json.loads(path.read_text())
        try:
            packet = Packet.model_validate(document)
        except ValidationError as exc:
            failed = True
            print(f"FAIL {path}")
            for err in exc.errors():
                print(f"  {'.'.join(map(str, err['loc']))}: {err['msg']}")
            continue
        root = path.parent if with_files else None
        report = check_packet(packet, root)
        if report.errors:
            failed = True
            print(f"FAIL {path}")
            for item in report.errors:
                print(f"  {item.pointer}: {item.message}")
        else:
            print(f"ok   {path}")
    return 1 if failed else 0


def _expand(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            packet = path / "packet.json"
            if packet.is_file():
                found.append(packet)
            else:
                found.extend(sorted(path.glob("packet.*.json")))
        else:
            found.append(path)
    return found


if __name__ == "__main__":
    sys.exit(main())
