"""Scene-schema compatibility receipts for the witness outputs.

Validates each written scene.json against the committed server schema
(`app/assets/schemas/scene.schema.json`). The input bundle's per-keyframe `depth` block is an
app-side input that the schema does not define and scene.build passes through to its keyframes;
when that is the only failure it is reported on its own line, not folded into a pass.

    uv run --with jsonschema python tests/scene_schema_check.py OUT1 [OUT2 ...]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA = Path(__file__).resolve().parents[2] / "server" / "schemas" / "scene.schema.json"


def split_errors(doc: dict) -> tuple[list, list]:
    """Top-level schema errors, and keyframe-`depth` extension errors kept apart."""
    validator = Draft202012Validator(json.loads(SCHEMA.read_text()))
    strict, depth_ext = [], []
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        if "Additional properties" in err.message and "'depth'" in err.message:
            depth_ext.append(err)
        else:
            strict.append(err)
    return strict, depth_ext


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("outputs", nargs="+", type=Path, help="run output folders with a scene.json")
    args = ap.parse_args()
    failures = 0
    for out in args.outputs:
        doc = json.loads((out / "scene.json").read_text())
        strict, depth_ext = split_errors(doc)
        print(
            f"{out}: {len(strict)} strict schema errors, {len(depth_ext)} keyframe-depth "
            "passthrough items (input extension, not defined by the schema)"
        )
        for e in strict:
            failures += 1
            print(f"  STRICT {list(e.absolute_path)}: {e.message[:200]}")
        for e in depth_ext[:3]:
            print(f"  depth passthrough at {list(e.absolute_path)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
