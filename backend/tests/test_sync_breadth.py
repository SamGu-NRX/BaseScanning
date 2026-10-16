"""sync_breadth.sh must anchor its mirrors under backend/, not the git toplevel.

Since the package moved into the BaseScanning monorepo (PR #247), the git
toplevel is the repository root one level above backend/. sync_breadth.sh
builds each breadth branch in a temporary worktree and mirrors the store
folder there with rsync --delete, so anchoring the mirror at the toplevel
copies into the app's own top-level recon/ directory and points rsync
--delete next to app files (Greptile finding on PR #247, comment 4281254460;
sync_from_store.sh got the same fix in PR #249). These tests pin that every
mirror lands below backend/, that rsync --delete prunes only inside the
mirrored package folder, and that files outside backend/ -- including a
sentinel in the app's top-level recon/ -- survive untouched.

The tests drive the script through a recording rsync stand-in on PATH rather
than the real binary (rsync is optional in dev environments). Real rsync
confines --delete to its destination directory, so the git-tree assertions
below carry the deletion-boundary proof; the stand-in additionally records
every destination the script asked rsync to write.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "sync_breadth.sh"

RSYNC_SHIM = """#!/usr/bin/env python3
\"\"\"Minimal rsync stand-in: mirrors a tree, honours --delete, --exclude and
--max-size, and logs every invocation to $RSYNC_LOG.\"\"\"
import fnmatch
import json
import os
import shutil
import sys
from pathlib import Path

args = sys.argv[1:]
excludes = []
src = None
dst = None
delete = False
max_size = None
rest = list(args)
while rest:
    arg = rest.pop(0)
    if arg == "--delete":
        delete = True
    elif arg.startswith("--exclude="):
        excludes.append(arg.split("=", 1)[1])
    elif arg == "--exclude":
        excludes.append(rest.pop(0))
    elif arg.startswith("--max-size="):
        value = arg.split("=", 1)[1]
        scale = {"k": 1024, "m": 1024**2, "g": 1024**3}.get(value[-1].lower())
        max_size = int(value[:-1]) * scale if scale else int(value)
    elif not arg.startswith("-") and src is None:
        src = arg
    elif not arg.startswith("-"):
        dst = arg

if os.environ.get("RSYNC_LOG"):
    with open(os.environ["RSYNC_LOG"], "a") as handle:
        handle.write(json.dumps({"src": src, "dst": dst, "delete": delete, "excludes": excludes}) + "\\n")


def excluded(rel: str, name: str) -> bool:
    low = rel.lower()
    for pattern in excludes:
        if name == pattern or fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern):
            return True
        base = pattern.strip("/")
        if "/" in base and (low == base or low.startswith(base + "/") or f"/{base}/" in f"/{low}/"):
            return True
    return False


def within(root: Path) -> dict[str, Path]:
    found = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if not excluded(rel, path.name):
            found[rel] = path
    return found


src_root = Path(src.rstrip("/"))
dst_root = Path(dst.rstrip("/"))
dst_root.mkdir(parents=True, exist_ok=True)
source = within(src_root)
for rel, path in source.items():
    target = dst_root / rel
    if path.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        continue
    if max_size is not None and path.stat().st_size > max_size:
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
if delete:
    for rel, path in within(dst_root).items():
        if rel not in source and path.is_file():
            path.unlink()
sys.exit(0)
"""


@dataclass
class Harness:
    """A throwaway monorepo with a bare origin, a fake store and the shim."""

    mono: Path
    store: Path
    log: Path
    env: dict[str, str]

    def git(self, *args: str, cwd: Path | None = None) -> str:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd or self.mono,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "backend/scripts/sync_breadth.sh", *args],
            cwd=self.mono,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def rsync_calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def tree(self, ref: str) -> set[str]:
        return set(self.git("ls-tree", "-r", "--name-only", ref).splitlines())

    def blob(self, ref: str, path: str) -> str:
        return self.git("rev-parse", f"{ref}:{path}")

    def ref_exists(self, ref: str) -> bool:
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", ref],
            cwd=self.mono,
            env=self.env,
            capture_output=True,
            text=True,
        )
        return probe.returncode == 0


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _harness(tmp_path: Path) -> Harness:
    mono = tmp_path / "mono"
    (mono / "backend" / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, mono / "backend" / "scripts" / "sync_breadth.sh")

    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(mono, "init", "-q", "-b", "main")
    _git(mono, "config", "user.email", "breadth-sync-test@example.com")
    _git(mono, "config", "user.name", "Breadth Sync Test")
    _git(mono, "config", "commit.gpgsign", "false")
    _git(mono, "remote", "add", "origin", str(origin))

    # App territory at the monorepo root, one level above backend/: a sentinel
    # in the app's own top-level recon/, exactly what a toplevel-anchored
    # mirror would trample.
    (mono / "recon").mkdir()
    (mono / "recon" / "APP-SENTINEL.txt").write_text("app file\n")
    (mono / "backend" / "README.md").write_text("# backend\n")
    _git(mono, "add", "-A")
    _git(mono, "commit", "-q", "-m", "monorepo seed")
    _git(mono, "push", "-q", "-u", "origin", "main")

    store = tmp_path / "store"
    folder = store / "internal" / "breadth" / "a1-classical"
    folder.mkdir(parents=True)
    (folder / "README.md").write_text("# a1-classical\n")
    (folder / "NOTES.md").write_text("notes\n")
    (folder / "code.py").write_text("x = 1\n")
    (store / "internal" / "breadth" / "A1-summary.md").write_text("# A1 result\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "rsync"
    shim.write_text(RSYNC_SHIM)
    shim.chmod(0o755)

    return Harness(
        mono=mono,
        store=store,
        log=tmp_path / "rsync-calls.jsonl",
        env={
            "PATH": f"{bin_dir}:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "STORE": str(store),
            "RSYNC_LOG": str(tmp_path / "rsync-calls.jsonl"),
        },
    )


def test_mirrors_land_inside_backend(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    result = h.run_script("--no-push", "a1-classical")
    assert result.returncode == 0, result.stderr

    calls = h.rsync_calls()
    assert len(calls) == 1
    assert calls[0]["delete"] is True
    # The script asked rsync to write at backend/recon/a1-classical/ inside the
    # throwaway worktree -- not at the worktree root's top-level recon/.
    assert calls[0]["dst"].rstrip("/").endswith("backend/recon/a1-classical")

    # Every file the sync added sits under backend/.
    added = h.tree("breadth/a1-classical") - h.tree("origin/main")
    assert added == {
        "backend/recon/a1-classical/README.md",
        "backend/recon/a1-classical/NOTES.md",
        "backend/recon/a1-classical/code.py",
        "backend/recon/a1-classical/RESULTS.md",
    }
    # The pre-fix script mirrored to the toplevel's recon/a1-classical/ instead.
    assert "recon/a1-classical" not in h.tree("breadth/a1-classical")

    # The app-root sentinel survives with identical content.
    assert h.blob("origin/main", "recon/APP-SENTINEL.txt") == h.blob("breadth/a1-classical", "recon/APP-SENTINEL.txt")

    # --no-push: nothing reached origin.
    assert not h.ref_exists("refs/remotes/origin/breadth/a1-classical")


def test_delete_boundary_stays_inside_the_mirrored_folder(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    result = h.run_script("--no-push", "a1-classical")
    assert result.returncode == 0, result.stderr

    # Publish the branch the way the script's push mode would, then leave three
    # kinds of cruft behind: a stale file inside the mirrored folder, a file
    # inside backend/ but outside it, and a file in the app's top-level recon/.
    h.git("push", "-q", "origin", "breadth/a1-classical")
    cruft = tmp_path / "cruft-wt"
    h.git("worktree", "add", "-q", str(cruft), "breadth/a1-classical")
    (cruft / "backend" / "recon" / "a1-classical" / "stale.txt").write_text("stale\n")
    (cruft / "backend" / "recon" / "a0-stray.txt").write_text("stray\n")
    (cruft / "recon" / "cruft.txt").write_text("app cruft\n")
    h.git("add", "-A", cwd=cruft)
    h.git("commit", "-q", "-m", "cruft", cwd=cruft)
    h.git("push", "-q", "origin", "breadth/a1-classical")
    h.git("worktree", "remove", str(cruft))

    # The store drops code.py between syncs.
    (h.store / "internal" / "breadth" / "a1-classical" / "code.py").unlink()
    h.log.unlink(missing_ok=True)

    result = h.run_script("--no-push", "a1-classical")
    assert result.returncode == 0, result.stderr

    calls = h.rsync_calls()
    assert len(calls) == 1
    assert calls[0]["delete"] is True

    branch = h.tree("breadth/a1-classical")
    # rsync --delete pruned inside the mirrored folder: the stale cruft and the
    # file the store dropped are gone.
    assert "backend/recon/a1-classical/stale.txt" not in branch
    assert "backend/recon/a1-classical/code.py" not in branch
    # ... and only there: the rest of backend/ and the app's top-level recon/
    # -- sentinel included -- survive untouched.
    assert "backend/recon/a1-classical/NOTES.md" in branch
    assert "backend/recon/a1-classical/README.md" in branch
    assert "backend/recon/a0-stray.txt" in branch
    assert "recon/cruft.txt" in branch
    assert "recon/APP-SENTINEL.txt" in branch
    assert h.blob("origin/main", "recon/APP-SENTINEL.txt") == h.blob("breadth/a1-classical", "recon/APP-SENTINEL.txt")
    # --no-push: the second sync commit stays local.
    assert h.git("rev-parse", "breadth/a1-classical") != h.git("rev-parse", "origin/breadth/a1-classical")
