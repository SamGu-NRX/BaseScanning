"""Run the shipped solver on the study's comparator scenes and emit its checks.

Usage: python extract_checks.py <server_dir> <scenes_json> <out_json>

<server_dir> is a checkout or `git archive` extraction whose server/ directory
holds rules.py, scene.py and solver.py. The script adds it to sys.path and
imports those modules by name, so the same script runs unmodified against
main or origin/t3/server, whatever that tree's code is.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def check_row(check: object) -> dict:
    row: dict = {}
    for field in ("id", "label", "outcome", "rule_key", "measured", "plus_minus", "threshold"):
        row[field] = getattr(check, field, None)
    return row


def main() -> None:
    server_dir, scenes_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.path.insert(0, str(Path(server_dir).resolve()))
    import rules as rules_mod
    import scene as scene_mod
    import solver as solver_mod

    loaded = rules_mod.load_rules()
    scenes = json.loads(Path(scenes_path).read_text())
    out = []
    for entry in scenes:
        sc = scene_mod.parse_scene(entry["scene"], loaded.rules)
        s = solver_mod.Solver(sc, loaded)
        cands = s.candidates(10.0)
        for c in cands:
            out.append(
                {
                    "scene": entry["name"],
                    "candidate": {"s0": getattr(c, "s0", None), "s1": getattr(c, "s1", None)},
                    "checks": [check_row(ch) for ch in getattr(c, "checks", [])],
                }
            )
    Path(out_path).write_text(json.dumps({"ref_loaded": server_dir, "candidates": out}, indent=1))


if __name__ == "__main__":
    main()
