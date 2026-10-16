"""LaMAR runner. The weekly job SKIPs when ``~/lamar`` is absent or poses are not merged."""

from __future__ import annotations

from pathlib import Path

from server.eval.pipeline_adapters import poses_run


def _poses_run():
    """WS-04, through the one allowed import edge."""
    try:
        return poses_run()
    except ImportError:
        return None


def available(data: Path | None = None) -> str | None:
    """Return a SKIP note, or None when a run could start."""
    root = data or (Path.home() / "lamar")
    if not root.is_dir():
        return f"SKIP LaMAR data is not at {root}"
    if _poses_run() is None:
        return "not_merged:ws-04"
    return None
