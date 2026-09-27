"""Stage stubs, the Modal module, and the import contracts."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

STAGES = [
    "packet",
    "poses",
    "objects",
    "reads",
    "scale",
    "dense",
    "coverage",
    "scene",
    "criteria",
    "result",
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


def test_api_and_orchestrator_stubs() -> None:
    api = importlib.import_module("server.api")
    orchestrator = importlib.import_module("server.orchestrator")
    with pytest.raises(NotImplementedError):
        api.create_app()
    with pytest.raises(NotImplementedError):
        orchestrator.sweep()
    assert orchestrator.validate_manifest is api.validate_manifest


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
