#!/usr/bin/env python3
"""Generate a deterministic, fully synthetic capture plan.

Everything this writes is synthetic by construction: paths under
synthetic/, seeded noise bytes (no photographs, no homeowner data, no
credentials). The same seed reproduces the same plan byte for byte, so
runs are comparable and replayable; a changed seed changes the actual
file bytes, not just the packet id.

The plan matches the published HouseScanKit contract: the create body
is the published CreateRequest (CaptureAPI.swift:20-27), the packet is
Packet 0.4 shaped (format "house-capture-packet", camelCase,
Packet04.swift:11-12; FileEntry path/bytes/sha256/role, :239-244), and
roles come from the published Role vocabulary - there is no mesh role
(Packet04.swift:235-236).

Usage:
  python gen_synthetic_packet.py --seed 1 --out synthetic-plan.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

# Published Role vocabulary, Packet04.swift:235-236.
ROLES = ("keyframe", "depth", "confidence", "features", "still", "stream", "other")


def noise_bytes(seed: int, tag: str, n: int) -> bytes:
    # Deterministic pseudo-content derived from seed AND path tag, so a
    # changed seed changes the actual payload bytes (recon §3r) while
    # staying reproducible across machines.
    digest = hashlib.sha256(f"{seed}:{tag}".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    return bytes(rng.randrange(256) for _ in range(n))


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", default="synthetic-plan.json")
    parser.add_argument("--file-count", type=int, default=3)
    parser.add_argument("--file-kb", type=int, default=64, help="per-file size in KiB")
    args = parser.parse_args()

    packet_id = f"synthetic-{args.seed:06d}"  # Packet04 packetId pattern :282-284
    size = args.file_kb * 1024
    roles: dict[str, str] = {}
    files: list[dict] = []
    for i in range(args.file_count):
        path = f"synthetic/scan{i:02d}.syn"
        role = ROLES[i % len(ROLES)]
        content = noise_bytes(args.seed, path, size)
        roles[path] = role
        files.append(
            {
                "path": path,
                "role": role,
                "bytes": size,
                "content_hex": content.hex(),
            }
        )

    # Packet 0.4 shaped finalize body (Packet04.swift:11-12, :239-244):
    # camelCase, format/formatVersion, FileEntry path/bytes/sha256/role.
    packet = {
        "format": "house-capture-packet",
        "formatVersion": "0.4",
        "packetId": packet_id,
        "files": [
            {
                "path": f["path"],
                "bytes": f["bytes"],
                "sha256": sha256_hex(bytes.fromhex(f["content_hex"])),
                "role": f["role"],
            }
            for f in files
        ],
    }
    # Published CreateRequest (CaptureAPI.swift:20-27).
    create_request = {
        "packetId": packet_id,
        "formatVersion": "0.4",
        "tier": "arkit",
        "flow": "guided",
        "device": {
            "model": "synthetic-fixture",
            "systemVersion": "0.0",
            "appVersion": "0.0.0",
        },
    }
    plan = {
        "plan": {
            "packet_id": packet_id,
            "create_request": create_request,
            "roles": roles,
            "files": files,
            "packet_hex": json.dumps(packet, sort_keys=True, separators=(",", ":"))
            .encode("utf-8")
            .hex(),
        },
        "generator": {
            "seed": args.seed,
            "identity": (
                f"gen_synthetic_packet seed={args.seed} "
                f"file_count={args.file_count} file_kb={args.file_kb}"
            ),
            "sha256_of_plan": None,
        },
    }
    blob = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    plan["generator"]["sha256_of_plan"] = hashlib.sha256(blob).hexdigest()

    out = Path(args.out)
    out.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} (plan sha256 {plan['generator']['sha256_of_plan']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
