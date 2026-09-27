"""Build a photo-only packet from fetched B2 images. Images are not committed.

SD-43: the shared synthetic packet is named explicitly as
`data/fixtures/packets/mini-arkit/packet.json` (WS-00). This script writes a
separate demo packet and refuses to invent sha256s when the photos are absent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SHARED_PACKET = Path("data/fixtures/packets/mini-arkit/packet.json")
SLOTS = (
    ("s1", "meter_close", "meter"),
    ("s2", "meter_oblique", "meter"),
    ("s3", "panel_open", "panel"),
    ("s4", "main_disconnect", "panel"),
)


def _find(root: Path, set_name: str, photo_id: str) -> Path | None:
    folder = root / set_name / "images"
    for suffix in (".jpg", ".jpeg", ".png"):
        path = folder / f"{photo_id}{suffix}"
        if path.exists():
            return path
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", default="")
    parser.add_argument("--meter", default="m01")
    parser.add_argument("--oblique", default="m03")
    parser.add_argument("--panel", default="p02")
    parser.add_argument("--disconnect", default="p12")
    parser.add_argument("--out", default="data/fixtures/reads/packet-reads-demo")
    args = parser.parse_args()
    print(f"shared stills fixture (SD-43): {SHARED_PACKET.as_posix()}")
    if not args.images:
        print("images not fetched; demo packet not written")
        return 0
    root = Path(args.images)
    wanted = {
        "s1": (args.meter, "meter"),
        "s2": (args.oblique, "meter"),
        "s3": (args.panel, "panel"),
        "s4": (args.disconnect, "panel"),
    }
    stills = []
    out = Path(args.out)
    files = out / "files"
    files.mkdir(parents=True, exist_ok=True)
    for still_id, purpose, _set_name in SLOTS:
        photo_id, set_name = wanted[still_id]
        image = _find(root, set_name, photo_id)
        if image is None:
            print(f"missing {set_name}/{photo_id}; demo packet not written")
            return 0
        data = image.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        name = f"{still_id}.jpg"
        (files / name).write_bytes(data)
        stills.append(
            {
                "id": still_id,
                "purpose": purpose,
                "path": f"files/{name}",
                "sha256": digest,
                "photo_id": photo_id,
            }
        )
    packet = {
        "format": "base-packet/v0.4",
        "kind": "photo_only",
        "capture_id": "cap_reads_demo",
        "stills": stills,
    }
    (out / "packet.json").write_text(json.dumps(packet, indent=1) + "\n")
    print(f"wrote {out / 'packet.json'} ({len(stills)} stills); do not commit files/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
