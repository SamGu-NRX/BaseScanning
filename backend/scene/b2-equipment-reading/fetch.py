"""Download the test photos: PR #16's meter manifest and PR #17's panel screen.

    python fetch.py meter  --manifest /tmp/house-scanning/experiments/meter-closeup/manifest.csv
    python fetch.py panel  --manifest /tmp/house-scanning/experiments/panel-label/screened.csv
    python fetch.py extra  --manifest panels_extra.csv

Photos go to $B2_DATA/<set>/images/<id>.jpg (default /tmp/b2data), upright (EXIF applied),
plain RGB, width capped at 3840 px as in PR #16/#17.
"""

import argparse
import csv
import io
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

DATA = Path(os.environ.get("B2_DATA", "/tmp/b2data"))
USER_AGENT = "house-scanning-b2-eval/0.1 (https://github.com/SamGu-NRX/house-scanning)"
MAX_WIDTH = 3840


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 3:
                raise
            time.sleep(30 * (attempt + 1))
    raise AssertionError("unreachable")


def save_upright(data: bytes, dest: Path) -> None:
    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        if image.width > MAX_WIDTH:
            image = image.resize((MAX_WIDTH, round(image.height * MAX_WIDTH / image.width)))
        image.save(dest, quality=95)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("set")
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    out = DATA / args.set / "images"
    out.mkdir(parents=True, exist_ok=True)
    with open(args.manifest) as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        if row.get("duplicate_of"):
            continue
        dest = out / f"{row['id']}.jpg"
        if dest.exists():
            continue
        try:
            save_upright(download(row["image_url"]), dest)
            print(f"fetched {row['id']}", flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"FAILED {row['id']}: {error}", flush=True)
        time.sleep(1.0)
    print(f"{len(list(out.glob('*.jpg')))} images in {out}")


if __name__ == "__main__":
    main()
