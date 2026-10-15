"""Replace meter-number fields with the literal REDACTED."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_NUMBER_KEYS = {"meter_number", "meternumber", "number_candidates"}


def redact(obj: dict) -> dict:
    return _walk(obj)


def contains_live_meter_number(value: object) -> bool:
    """True when a meter-number field is present and is not already REDACTED."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.lower().replace("_", "")
            if key.lower() in _NUMBER_KEYS or normalized == "meternumber":
                if item not in (None, "REDACTED"):
                    return True
            elif contains_live_meter_number(item):
                return True
        return False
    if isinstance(value, list):
        return any(contains_live_meter_number(item) for item in value)
    return False


def _walk(value: object) -> object:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key.lower() in _NUMBER_KEYS or key.lower().replace("_", "") == "meternumber":
                cleaned[key] = "REDACTED"
            else:
                cleaned[key] = _walk(item)
        return cleaned
    if isinstance(value, list):
        return [_walk(item) for item in value]
    return value


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: redact.py <in.json> <out.json>", file=sys.stderr)
        return 2
    source = Path(args[0])
    if not source.is_file():
        print(f"missing input: {source}", file=sys.stderr)
        return 3
    document = json.loads(source.read_text())
    if not isinstance(document, dict):
        print("input must be a JSON object", file=sys.stderr)
        return 2
    Path(args[1]).write_text(json.dumps(redact(document), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
