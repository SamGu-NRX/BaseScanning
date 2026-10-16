"""Frame uploads must name the Modal environment. Flagless `volume put` uses the workspace default."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _scene():
    path = Path(__file__).resolve().parents[2] / "scripts" / "modal_scene.py"
    spec = importlib.util.spec_from_file_location("modal_scene_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_volume_put_passes_env_flag():
    scene = _scene()
    argv = scene.volume_put_argv("/tmp/yt", "in/run/yt", "dev")
    assert argv[argv.index("--force") + 1 : argv.index("--force") + 3] == ["-e", "dev"]
    assert "house-capture-pipeline" in argv
    assert argv[-2:] == ["/tmp/yt", "in/run/yt"]


def test_volume_put_without_env_omits_the_flag():
    scene = _scene()
    argv = scene.volume_put_argv("/tmp/yt", "in/frame.jpg", "")
    assert "-e" not in argv
