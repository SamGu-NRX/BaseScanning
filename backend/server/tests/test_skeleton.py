"""Stage stubs, the Modal module, and the import contracts."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from server.common.config import Settings

# `result` is implemented in this package. `reads` is implemented by WS-08.
# Neither name is in this stub list. The other names are still stubs.
STAGES = [
    "packet",
    "poses",
    "objects",
    "scale",
    "dense",
    "coverage",
    "scene",
    "criteria",
]


@pytest.mark.parametrize("name", STAGES)
def test_stage_run_is_unimplemented(name: str) -> None:
    module = importlib.import_module(f"server.{name}")
    with pytest.raises(NotImplementedError):
        module.run(None)  # type: ignore[attr-defined]


def test_objects_3d_is_unimplemented() -> None:
    objects = importlib.import_module("server.objects")
    with pytest.raises(NotImplementedError):
        objects.run_3d(None)


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
