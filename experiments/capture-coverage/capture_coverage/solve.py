"""Solve a produced scene.json with the real placement server at a given checkout.

The driver script runs inside the server's uv environment (its own pydantic/pyyaml versions),
loads rules, parses the scene, and prints the result JSON. Two refs are compared: the working
tree's server/ and an extraction of t3/server at 1d1e7e1b (git archive - no git state changes).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

T3_SERVER_REF = "1d1e7e1b55237ae82f5af10125ca33818f15cfce"

DRIVER = '''
import json, sys
sys.path.insert(0, sys.argv[1])
from rules import load_rules          # noqa: E402
from scene import parse_scene         # noqa: E402
from solver import solve              # noqa: E402
raw = json.load(open(sys.argv[2]))
loaded = load_rules()
result = solve(parse_scene(raw, loaded.rules), loaded)
print(json.dumps(result, default=str))
'''


def solve_scene(scene_path: Path, server_dir: Path, repo_root: Path, tag: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix="_driver.py", delete=False) as f:
        f.write(DRIVER)
        driver = Path(f.name)
    try:
        cmd = [
            "uv", "run", "--project", str(server_dir),
            "python", str(driver), str(server_dir), str(scene_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(repo_root))
        if proc.returncode != 0:
            return {"error": proc.stderr[-2000:], "decision": None}
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(driver)


def ensure_t3_server(repo_root: Path) -> Path:
    """Extract server/ at the t3/server ref with git archive into a scratch dir (read-only w.r.t.
    the working tree: archive writes nothing into .git or the checkout)."""
    target = Path(tempfile.gettempdir()) / f"cc-t3server-{T3_SERVER_REF[:8]}"
    if not (target / "server" / "pyproject.toml").exists():
        target.mkdir(parents=True, exist_ok=True)
        p = subprocess.run(
            ["git", "archive", T3_SERVER_REF, "server"],
            cwd=str(repo_root), check=True, stdout=subprocess.PIPE,
        )
        subprocess.run(["tar", "-x", "-C", str(target)], input=p.stdout, check=True)
    return target / "server"
