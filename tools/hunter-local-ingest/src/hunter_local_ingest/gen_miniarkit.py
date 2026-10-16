"""Build a capture plan from the committed mini-arkit packet.

Reads ``backend/data/fixtures/packets/mini-arkit`` from a repo checkout:
the committed ``packet.json`` plus every file it lists. Each file is read
from disk and digested again, so a drifted or missing fixture fails here,
at build time, instead of at finalize. The document mirrors the synthetic
family's ``plan`` section; the ``generator`` section is
``gen_miniarkit_packet`` and carries no seed — the fixture, not a seed,
determines the bytes.

The committed packet's ``source.kind`` is ``synthetic``: the repo ships it
as site-shaped fixture data, never a real capture.

Usage:
  hunter-gen-miniarkit --out miniarkit-plan.json
  python fixtures/gen_miniarkit_plan.py --out miniarkit-plan.json  (repo checkout)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

from .plan import MINIARKIT_PACKET_ID, canonical_digest

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".bin": "application/octet-stream",
    ".csv.gz": "application/gzip",
}
_ROLES = {"keyframe", "features", "stream"}


def default_fixture_dir() -> Path | None:
    """The committed packet, when this package sits in a repo checkout."""
    candidate = (
        Path(__file__).resolve().parents[4]
        / "backend"
        / "data"
        / "fixtures"
        / "packets"
        / "mini-arkit"
    )
    return candidate if candidate.is_dir() else None


def build(fixture_dir: Path) -> dict:
    """Read the committed packet and its files into a loadable plan document."""
    raw_packet = (fixture_dir / "packet.json").read_bytes()
    parsed = json.loads(raw_packet.decode("utf-8"))
    if parsed.get("packetId") != MINIARKIT_PACKET_ID:
        raise SystemExit("the committed packet.json is not the mini-arkit packet")
    session = parsed.get("session") or {}
    if session.get("tier") != "arkit" or not parsed.get("keyframes"):
        raise SystemExit("the committed packet is not an arkit packet with keyframes")
    if (parsed.get("source") or {}).get("kind") != "synthetic":
        raise SystemExit("the committed packet is not synthetic by construction")

    files: list[dict] = []
    entries: list[dict] = []
    for entry in parsed["files"]:
        path = entry["path"]
        content = (fixture_dir / path).read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        if entry.get("bytes") != len(content) or entry.get("sha256") != sha:
            raise SystemExit(f"fixture file {path} drifts from packet.json")
        content_type = next(
            (value for suffix, value in _CONTENT_TYPES.items() if path.endswith(suffix)), None
        )
        if content_type is None or entry.get("role") not in _ROLES:
            raise SystemExit(f"fixture file {path} has an unexpected role or content type")
        files.append(
            {
                "path": path,
                "role": entry["role"],
                "contentType": content_type,
                "bytes": len(content),
                "sha256": sha,
                "md5": base64.b64encode(
                    hashlib.md5(content, usedforsecurity=False).digest()
                ).decode(),
                "content_hex": content.hex(),
            }
        )
        entries.append({"path": path, "bytes": len(content), "sha256": sha, "role": entry["role"]})

    document = {
        "format": "hunter-local-ingest-plan",
        "format_version": 2,
        "generator": {"name": "gen_miniarkit_packet", "sha256_of_plan": None},
        "plan": {
            "packet_id": parsed["packetId"],
            # CaptureAPI.CreateRequest, from the packet's own session.
            "create_request": {
                "packetId": parsed["packetId"],
                "formatVersion": "0.4",
                "tier": session["tier"],
                "flow": "guided",
                "device": {
                    "model": session["deviceModel"],
                    "systemVersion": session["systemVersion"],
                    "appVersion": session["appVersion"],
                },
            },
            "files": files,
            "packet_hex": raw_packet.hex(),
            "packet_sha256": hashlib.sha256(raw_packet).hexdigest(),
        },
    }
    document["generator"]["sha256_of_plan"] = canonical_digest(document)
    return document


def write_plan(out: Path, fixture_dir: Path) -> dict:
    document = build(fixture_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(prog="hunter-gen-miniarkit", description=__doc__)
    parser.add_argument("--out", default="miniarkit-plan.json")
    parser.add_argument(
        "--fixture-dir",
        default=None,
        help="the committed mini-arkit packet directory (default: repo checkout path)",
    )
    args = parser.parse_args()
    fixture_dir = Path(args.fixture_dir) if args.fixture_dir else default_fixture_dir()
    if fixture_dir is None or not fixture_dir.is_dir():
        parser.error("run from a repo checkout: backend/data/fixtures/packets/mini-arkit not found")

    out = Path(args.out)
    document = write_plan(out, fixture_dir)
    print(f"wrote {out.name} (plan sha256 {document['generator']['sha256_of_plan']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
