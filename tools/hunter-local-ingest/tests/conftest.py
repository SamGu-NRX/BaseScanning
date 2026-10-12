"""Shared fixtures: a generated plan, the fake API, and a runner helper.

Canary markers are assembled at runtime from a fixed prefix and random hex.
They are synthetic; nothing here is a real credential.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from hunter_local_ingest.fakeapi import FakeApi, FakeConfig
from hunter_local_ingest.fixturegen import write_plan
from hunter_local_ingest.plan import load_plan
from hunter_local_ingest.runner import run

FAST = [
    "--poll-interval-s",
    "0.01",
    "--events-wait-s",
    "0",
    "--retry-backoff-s",
    "0",
    "--result-budget-s",
    "5",
]


def canary(tag: str) -> str:
    return f"CANARY-{tag}-{uuid.uuid4().hex[:12]}"


@pytest.fixture()
def plan_path(tmp_path: Path) -> Path:
    path = tmp_path / "plan.json"
    write_plan(path, seed=7, mesh_count=3, mesh_kb=4)
    return path


@pytest.fixture()
def plan(plan_path: Path):
    return load_plan(plan_path).plan


@pytest.fixture()
def fake():
    """Start a FakeApi with an optional config: ``with fake(cfg) as api``."""

    def start(config: FakeConfig | None = None) -> FakeApi:
        return FakeApi(config)

    return start


def run_cli(argv: list[str]) -> int:
    return run(argv)


def only_run_dir(out: Path) -> Path:
    dirs = [p for p in out.iterdir() if p.is_dir()]
    assert len(dirs) == 1, dirs
    return dirs[0]


def read_checks(run_dir: Path) -> dict[str, dict]:
    lines = [json.loads(line) for line in (run_dir / "checks.jsonl").read_text().splitlines()]
    return {line["id"]: line for line in lines}


def all_strings(value) -> list[str]:
    """Every string in a JSON value: keys, values, nested at any depth."""
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            out.append(str(key))
            out.extend(all_strings(item))
    elif isinstance(value, list):
        for item in value:
            out.extend(all_strings(item))
    return out


def artifact_values(run_dir: Path) -> list[str]:
    """Every string in every JSON artifact of a run, parsed (not regex)."""
    strings: list[str] = []
    for path in run_dir.iterdir():
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            for line in text.splitlines():
                strings.extend(all_strings(json.loads(line)))
        else:
            strings.extend(all_strings(json.loads(text)))
    return strings
