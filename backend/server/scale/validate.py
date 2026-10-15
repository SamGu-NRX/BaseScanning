"""Validate ``scale/scale.json`` against the scale block and the extension schema."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import jsonschema

from server.contracts.scale import ScaleBlock

_SCHEMA = Path(__file__).resolve().parent / "schemas" / "scale-ext.v0.schema.json"


def validate_file(path: Path) -> ScaleBlock:
    payload = json.loads(Path(path).read_text())
    jsonschema.validate(payload, json.loads(_SCHEMA.read_text()))
    return ScaleBlock.model_validate(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a scale block.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        validate_file(args.path)
    except (OSError, json.JSONDecodeError, jsonschema.ValidationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
