"""Stage stubs, the Modal module, and the import contracts."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from server.common.config import Settings

# packet (WS-01), reads (WS-08), objects (WS-07), scale (WS-05), result (WS-10),
# criteria (WS-09), and dense/coverage/scene (WS-06) are implemented.
# poses still raises.
UNIMPLEMENTED = ["poses"]
IMPLEMENTED = [
    "packet",
    "reads",
    "objects",
    "scale",
    "criteria",
    "dense",
    "coverage",
    "scene",
    "result",
]


def test_scale_run_is_the_stage() -> None:
    """WS-05 replaced the scale stub."""

    scale = importlib.import_module("server.scale")
    assert scale.STAGE == "scale"
    assert callable(scale.run)


@pytest.mark.parametrize("name", UNIMPLEMENTED)
def test_stage_run_is_unimplemented(name: str) -> None:
    module = importlib.import_module(f"server.{name}")
    with pytest.raises(NotImplementedError):
        module.run(None)  # type: ignore[attr-defined]


@pytest.mark.parametrize("name", IMPLEMENTED)
def test_stage_run_is_implemented(name: str) -> None:
    module = importlib.import_module(f"server.{name}")
    assert callable(module.run)


def test_objects_stage_is_implemented() -> None:
    """WS-07 replaced the skeleton stub."""
    objects = importlib.import_module("server.objects")
    assert objects.run.__module__ == "server.objects.run"
    assert objects.run_3d.__module__ == "server.objects.run"


def test_api_app_and_orchestrator_stub() -> None:
    api = importlib.import_module("server.api")
    orchestrator = importlib.import_module("server.orchestrator")
    app = api.create_app(settings=Settings(SMP_ENVIRONMENT="dev", VISION_EXTERNAL_CALLS="on"))
    assert app.title == "capture-api"
    assert callable(orchestrator.start_run)
    assert callable(orchestrator.sweep)
    assert callable(orchestrator.validate_manifest)


def test_modal_app_imports_without_a_token(tmp_path: Path) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("MODAL")}
    env["HOME"] = str(tmp_path)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import server.modal_app as app; assert app.APP_NAME == 'house-scanning'; assert app.smoke_cpu is not None",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_import_contracts() -> None:
    completed = subprocess.run(["lint-imports"], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
