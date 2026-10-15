"""Run PR #11's placement solver (t3/server) on a scene.json, in process.

    python run_pr11.py SERVER_DIR scene.json out.json [--rules demo|strict]

`strict` merges pr11_strictest.yaml (the strictest of Base's three profiles, same numbers as the C2
rules) over the public rules.yaml through the server's own private-rules mechanism.
"""

import json
import os
import sys
import time
from pathlib import Path

server_dir, scene_path, out_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
mode = sys.argv[5] if len(sys.argv) > 5 and sys.argv[4] == "--rules" else "demo"
if mode == "strict":
    os.environ["HOUSESCAN_PRIVATE_RULES"] = str(Path(__file__).resolve().parent / "pr11_strictest.yaml")
else:
    os.environ["HOUSESCAN_PRIVATE_RULES"] = "/nonexistent"
sys.path.insert(0, str(server_dir))
from rules import load_rules  # noqa: E402
from scene import parse_scene  # noqa: E402
from solver import solve  # noqa: E402

loaded = load_rules(None if mode == "strict" else Path("/nonexistent"))
raw_bytes = scene_path.read_bytes()
t0 = time.perf_counter()
scene = parse_scene(json.loads(raw_bytes), loaded.rules, raw_bytes)
result = solve(scene, loaded, budget_s=120)
result["_runner"] = {"rules": mode, "seconds": round(time.perf_counter() - t0, 3)}
out_path.write_text(json.dumps(result, indent=1))
spot = result.get("spot") or {}
print(mode, result["decision"], "spot:", spot.get("wall_id"), spot.get("start_ft"),
      "reasons:", [r.get("code") for r in result.get("reasons", [])], f"{time.perf_counter() - t0:.2f}s")
