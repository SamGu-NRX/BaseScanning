"""sync_from_store.sh must anchor its copies under backend/, not the git toplevel.

Since the package moved into the BaseScanning monorepo (PR #247), the git
toplevel is the repository root one level above backend/. Anchoring the
mirrors at the toplevel would rsync --delete into the app's own top-level
packet/ and docs/ folders (Greptile finding on scripts/sync_from_store.sh:26).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "sync_from_store.sh"


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def test_sync_writes_inside_backend_only(tmp_path: Path) -> None:
    if shutil.which("rsync") is None:  # pragma: no cover - environment guard
        import pytest

        pytest.skip("rsync is not installed")
    mono = tmp_path / "mono"
    (mono / "backend" / "scripts").mkdir(parents=True)
    _run(["git", "init", "-q"], mono)

    # The app's own top-level folders at the monorepo root must never be touched.
    root_packet = mono / "packet"
    root_packet.mkdir()
    (root_packet / "keep.txt").write_text("app file\n")

    # A stale backend file absent from the store: rsync --delete removes it,
    # proving the mirror really landed in backend/packet/.
    stale = mono / "backend" / "packet"
    stale.mkdir()
    (stale / "stale-only.txt").write_text("stale\n")

    store = tmp_path / "store"
    for folder in (
        "internal/packet-generator",
        "internal/eval-harness",
        "internal/criteria",
        "internal/data/scripts",
        "docs",
    ):
        (store / folder).mkdir(parents=True)
    (store / "internal/packet-generator/packet.json").write_text("{}\n")
    (store / "internal/eval-harness/harness.py").write_text("# eval\n")
    (store / "internal/criteria/criteria.yaml").write_text("criteria: {}\n")
    (store / "internal/data/scripts/build.py").write_text("# data tooling\n")
    (store / "internal/data/manifest.json").write_text("{}\n")
    (store / "docs/capture-packet-spec.md").write_text("# spec\n")
    (store / "internal/criteria/SSR_Prompts.md").write_text("private\n")

    shutil.copy2(SCRIPT, mono / "backend" / "scripts" / "sync_from_store.sh")

    result = subprocess.run(
        ["bash", "backend/scripts/sync_from_store.sh", "--here"],
        cwd=mono,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "STORE": str(store), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    # Copies anchor under the package root.
    assert (mono / "backend" / "packet" / "packet.json").is_file()
    assert (mono / "backend" / "eval" / "harness.py").is_file()
    assert (mono / "backend" / "criteria" / "spec" / "criteria.yaml").is_file()
    assert (mono / "backend" / "data" / "scripts" / "build.py").is_file()
    assert (mono / "backend" / "data" / "manifest.json").is_file()
    assert (mono / "backend" / "docs" / "capture-packet-spec.md").is_file()
    # Base prompt material stays under the package's ignored private/ folder.
    assert (mono / "backend" / "private" / "criteria" / "SSR_Prompts.md").is_file()
    # rsync --delete applies inside the package mirror only.
    assert not (mono / "backend" / "packet" / "stale-only.txt").exists()
    # Nothing was written outside backend/.
    assert (root_packet / "keep.txt").is_file()
    assert list(root_packet.iterdir()) == [root_packet / "keep.txt"]
    assert not (mono / "docs").exists()
    assert not (mono / "eval").exists()
