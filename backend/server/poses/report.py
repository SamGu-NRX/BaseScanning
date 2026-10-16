"""Registration report: schema check and on-disk writers."""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import jsonschema
import numpy as np

SCHEMA_PATH = Path(__file__).with_name("schemas") / "registration-report.v0.schema.json"


def validate_report(path: Path) -> None:
    report = json.loads(Path(path).read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.validate(report, schema)


def write_ply(path: Path, xyz: np.ndarray, rgb: np.ndarray, n_views: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(xyz)
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {n}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "property uchar red\n"
        "property uchar green\n"
        "property uchar blue\n"
        "property int n_views\n"
        "end_header\n"
    )
    with path.open("wb") as handle:
        handle.write(header.encode("ascii"))
        for i in range(n):
            handle.write(struct.pack("<fff", float(xyz[i, 0]), float(xyz[i, 1]), float(xyz[i, 2])))
            handle.write(struct.pack("<BBB", int(rgb[i, 0]), int(rgb[i, 1]), int(rgb[i, 2])))
            handle.write(struct.pack("<i", int(n_views[i])))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Check a registration report against its schema")
    parser.add_argument("--check", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        validate_report(args.check)
    except (jsonschema.ValidationError, json.JSONDecodeError, OSError) as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
    print("ok", args.check)


if __name__ == "__main__":
    main()
