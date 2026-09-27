"""Build a photo-only v0.4 packet from fetched B2 images. Images are not committed.

SD-43: the shared synthetic packet is named explicitly as
`data/fixtures/packets/mini-arkit/packet.json` (WS-00). This script writes a
separate demo packet and refuses to invent sha256s when the photos are absent.
The written `packet.json` is a contract v0.4 `house-capture-packet`.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from server.contracts.packet import Packet  # noqa: E402

SHARED_PACKET = Path("data/fixtures/packets/mini-arkit/packet.json")
SLOTS = (
    ("s1", "meter_close", "meter"),
    ("s2", "meter_oblique", "meter"),
    ("s3", "panel_open", "panel"),
    ("s4", "main_disconnect", "panel"),
)
PACKET_ID = "reads-demo-packet"


def _find(root: Path, set_name: str, photo_id: str) -> Path | None:
    folder = root / set_name / "images"
    for suffix in (".jpg", ".jpeg", ".png"):
        path = folder / f"{photo_id}{suffix}"
        if path.exists():
            return path
    return None


def build_packet(stills: list[dict], files: list[dict]) -> dict:
    """A photo_only packet the v0.4 `Packet` model accepts."""
    body = {
        "format": "house-capture-packet",
        "formatVersion": "0.4",
        "packetId": PACKET_ID,
        "createdAt": "2026-09-27T00:00:00Z",
        "units": {
            "length": "meters",
            "angle": "degrees",
            "time": "seconds of device uptime",
            "image": "pixels of the saved JPEG",
        },
        "conventions": {
            "world": "ARKit gravity world, y up",
            "camera": "ARKit camera, looks along -z",
            "pose": "camera-to-world 4x4, column major, meters",
            "intrinsics": "[fx, fy, cx, cy] in pixels",
            "pixel": "[u, v] top-left origin, v down",
        },
        "source": {"kind": "synthetic", "generator": "reads_fixture_packet"},
        "session": {
            "id": "reads-demo-session",
            "startedAt": "2026-09-27T00:00:00Z",
            "startedAtUptime": 0,
            "appVersion": "reads-demo",
            "deviceModel": "synthetic",
            "systemVersion": "0",
            "tier": "photo_only",
            "lidarAvailable": False,
            "sceneDepthEnabled": False,
        },
        "keyframes": [],
        "stills": stills,
        "files": files,
    }
    return Packet.model_validate(body).model_dump(mode="json", exclude_none=True)


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
    stills: list[dict] = []
    files: list[dict] = []
    out = Path(args.out)
    folder = out / "stills"
    folder.mkdir(parents=True, exist_ok=True)
    for still_id, purpose, _set_name in SLOTS:
        photo_id, set_name = wanted[still_id]
        image = _find(root, set_name, photo_id)
        if image is None:
            print(f"missing {set_name}/{photo_id}; demo packet not written")
            return 0
        data = image.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        name = f"{still_id}.jpg"
        (folder / name).write_bytes(data)
        with Image.open(io.BytesIO(data)) as opened:
            width, height = opened.size
        rel = f"stills/{name}"
        stills.append(
            {
                "id": still_id,
                "purpose": purpose,
                "img": rel,
                "w": width,
                "h": height,
                "timestamp": 0,
                "orientation": 1,
            }
        )
        files.append({"path": rel, "bytes": len(data), "sha256": digest, "role": "still"})
    packet = build_packet(stills, files)
    (out / "packet.json").write_text(json.dumps(packet, indent=1) + "\n")
    print(f"wrote {out / 'packet.json'} ({len(stills)} stills); do not commit stills/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
