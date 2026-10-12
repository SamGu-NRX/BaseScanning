"""What code and runtime produced a run.

``source_sha256`` digests every file of the imported package (relative
path and bytes, sorted), so it names the code that actually ran, editable
or installed. When the package is imported from a tracked git checkout,
the manifest also records HEAD, whether the tool's directory differs from
HEAD, a digest of that diff and the count of untracked files there. An
installed copy outside any checkout records no git state rather than
borrowing the state of whatever directory it sits in.

Git is only read: ``GIT_OPTIONAL_LOCKS=0`` keeps status from writing the
index. No path is recorded.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import subprocess
from pathlib import Path

from .http_client import child_env

PACKAGE_DIR = Path(__file__).resolve().parent
_TEXT_RE = re.compile(r"[^A-Za-z0-9._+-]")


def source_sha256(package_dir: Path = PACKAGE_DIR) -> str:
    digest = hashlib.sha256()
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = path.relative_to(package_dir).as_posix()
        digest.update(rel.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _git(args: list[str], cwd: Path) -> bytes | None:
    env = {**child_env(), "GIT_OPTIONAL_LOCKS": "0"}
    try:
        proc = subprocess.run(  # noqa: S603, S607 - fixed argv, read-only git
            ["git", *args], cwd=cwd, env=env, capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def git_state(package_dir: Path = PACKAGE_DIR) -> dict | None:
    """HEAD and dirtiness of the tool directory, if this copy is tracked."""
    if _git(["ls-files", "--error-unmatch", "__init__.py"], package_dir) is None:
        return None
    head = _git(["rev-parse", "HEAD"], package_dir)
    tool_root = package_dir.parents[1]  # tools/hunter-local-ingest
    diff = _git(["diff", "HEAD", "--binary", "--", "."], tool_root)
    untracked = _git(["ls-files", "--others", "--exclude-standard", "--", "."], tool_root)
    if head is None or diff is None or untracked is None:
        return None
    head_text = head.decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head_text):
        return None
    untracked_count = len([line for line in untracked.splitlines() if line.strip()])
    return {
        "head": head_text,
        "dirty": bool(diff) or untracked_count > 0,
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
        "untracked": untracked_count,
    }


def _clean(text: str) -> str:
    return (_TEXT_RE.sub("-", text) or "unknown")[:40]


def runtime() -> dict:
    return {
        "python": _clean(platform.python_version()),
        "implementation": _clean(platform.python_implementation()),
        "system": _clean(platform.system()),
        "release": _clean(platform.release()),
        "machine": _clean(platform.machine()),
        "cpus": os.cpu_count(),
    }
