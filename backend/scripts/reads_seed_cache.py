"""Copy stored reader answers into the local cache. Does not print answer text.

Image bytes are hashed only when `--images` contains the photo. Without them the
script writes nothing: a cache key is the file sha256, and inventing one would
not match a later fetch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.reads.cache import local_cache_path, write_local_cache  # noqa: E402
from server.reads.client import cost_usd  # noqa: E402
from server.reads.types import ReaderAnswer  # noqa: E402

PROMPTS = {
    "meter": "meter/v0",
    "panel": "panel/v0",
    "extra": "panel/v0",
}
MODELS = {
    "meter": ("gpt-5.5", "grok-4.7"),
    "panel": ("gpt-5.4-mini", "gpt-5.5"),
    "extra": ("gpt-5.4-mini", "gpt-5.5"),
}


def _load(path: Path) -> dict[str, dict]:
    found: dict[str, dict] = {}
    if not path.exists():
        return found
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "error" in record or record.get("answer") is None:
            continue
        found[str(record["id"])] = record
    return found


def _answer(record: dict, model: str, prompt_id: str) -> ReaderAnswer:
    usage = record.get("usage") or {}
    return ReaderAnswer(
        model=model,
        prompt_id=prompt_id,
        answer=record.get("answer"),
        raw_text="",
        secs=float(record.get("secs") or 0),
        usage=usage,
        cost_usd=cost_usd(model, usage),
        cached=True,
    )


def _image(root: Path, set_name: str, photo_id: str) -> Path | None:
    folder = root / set_name / "images"
    for suffix in (".jpg", ".jpeg", ".png"):
        path = folder / f"{photo_id}{suffix}"
        if path.exists():
            return path
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True)
    parser.add_argument("--images", default="")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--max-side", type=int, default=2048)
    args = parser.parse_args()
    raw = Path(args.raw)
    images = Path(args.images) if args.images else None
    written = 0
    skipped = 0
    for set_name, models in MODELS.items():
        prompt_id = PROMPTS[set_name]
        by_model = {model: _load(raw / set_name / f"vlm_{model}.jsonl") for model in models}
        photo_ids = set().union(*by_model.values()) if by_model else set()
        for photo_id in sorted(photo_ids):
            image = _image(images, set_name, photo_id) if images is not None else None
            if image is None:
                skipped += 1
                continue
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            for model in models:
                record = by_model[model].get(photo_id)
                if record is None:
                    continue
                path = local_cache_path(args.cache, digest, model, prompt_id, args.max_side)
                write_local_cache(path, _answer(record, model, prompt_id))
                written += 1
    print(f"seeded {written} cache files, skipped {skipped} photos without an image")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
