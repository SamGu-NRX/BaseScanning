#!/usr/bin/env python3
"""Generate a deterministic, fully synthetic capture plan.

Everything this writes is synthetic by construction: paths under
fixtures/synthetic/, seeded noise bytes (no photographs, no homeowner
data, no credentials). The same seed reproduces the same plan byte for
byte, so runs are comparable and replayable.

Usage:
  python gen_synthetic_packet.py --seed 1 --out synthetic-plan.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path


def noise_bytes(rng: random.Random, n: int, tag: str) -> bytes:
    # Deterministic pseudo-content, tagged so contents differ per role but
    # stay reproducible across machines.
    rng_state = rng.getstate()
    seed_int = int.from_bytes(hashlib.sha256(tag.encode()).digest()[:4], "big")
    rng.seed(seed_int)
    payload = bytes(rng.randrange(256) for _ in range(n))
    rng.setstate(rng_state)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", default="synthetic-plan.json")
    parser.add_argument("--mesh-count", type=int, default=3)
    parser.add_argument("--mesh-kb", type=int, default=64, help="per-file size in KiB")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    packet_id = f"synthetic-{args.seed:06d}"
    roles = {}
    files = []
    for i in range(args.mesh_count):
        path = f"synthetic/scan{i:02d}.syn"
        roles[path] = "mesh"
        files.append(
            {
                "path": path,
                "role": "mesh",
                "bytes": args.mesh_kb * 1024,
                "content_hex": noise_bytes(rng, args.mesh_kb * 1024, path).hex(),
            }
        )

    packet = {
        "schema_version": "0.4",
        "packet_id": packet_id,
        "files": [f["path"] for f in files],
        "note": "synthetic fixture; no homeowner data",
    }
    plan = {
        "plan": {
            "packet_id": packet_id,
            "create_request": {
                "source": "synthetic-fixture",
                "schema_version": "0.4",
            },
            "roles": roles,
            "files": files,
            "packet_hex": json.dumps(packet).encode("utf-8").hex(),
        },
        "generator": {
            "seed": args.seed,
            "identity": (
                f"gen_synthetic_packet seed={args.seed} "
                f"mesh_count={args.mesh_count} mesh_kb={args.mesh_kb}"
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
