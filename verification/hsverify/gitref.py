"""Read-only access to other branches through throwaway worktrees under /tmp.

Verification never checks out another thread's branch in its own worktree and never
pushes to one. A detached worktree at the exact commit keeps the evidence tied to a SHA.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP_ROOT = Path("/tmp")


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def fetch() -> None:
    git("fetch", "--quiet", "--prune", "origin")


def resolve(ref: str) -> str:
    """Full SHA of a ref. Raises CalledProcessError with git's message if it does not exist."""
    return git("rev-parse", "--verify", f"{ref}^{{commit}}")


def ref_exists(ref: str) -> bool:
    try:
        resolve(ref)
    except subprocess.CalledProcessError:
        return False
    return True


def show(ref: str, path: str) -> str | None:
    """File contents at a ref, or None when the file does not exist there."""
    try:
        return git("show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return None


@contextlib.contextmanager
def detached_worktree(sha: str, keep: bool = False) -> Iterator[Path]:
    """A detached worktree of `sha` under /tmp, removed afterwards unless `keep`.

    The path is unique per call: several checks (and the scoreboard) run at once on this Mac,
    and a path shared by SHA let one run delete a tree another was still using.
    """
    path = Path(tempfile.mkdtemp(prefix=f"hs-verify-{sha[:12]}-", dir=TMP_ROOT))
    path.rmdir()  # git creates it
    git("worktree", "add", "--detach", "--force", str(path), sha)
    try:
        yield path
    finally:
        if not keep:
            try:
                git("worktree", "remove", "--force", str(path))
            except subprocess.CalledProcessError:
                shutil.rmtree(path, ignore_errors=True)
                git("worktree", "prune")
