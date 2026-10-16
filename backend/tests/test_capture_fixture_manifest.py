"""The manifest guard over the capture journey's ``mini-arkit`` fixture.

The committed packet check runs with ``--no-files``, so the keyframes and streams
the journey test copies could disappear from the repository and ``make validate``
would still pass, leaving a raw ``FileNotFoundError`` inside
``test_capture_fixture_flow`` as the only symptom. These tests pin the seam:
the committed fixture passes the guard, and an incomplete temporary copy fails
with the missing input named.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "validate_fixtures.py"
FIXTURES = REPO / "data" / "fixtures"


def _guard():
    spec = importlib.util.spec_from_file_location("validate_fixtures", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.capture_journey_errors


@pytest.fixture
def guard():
    return _guard()


@pytest.fixture
def copied_fixtures(tmp_path: Path) -> Path:
    """A temporary copy of just the manifest and the capture-journey packet."""
    root = tmp_path / "fixtures"
    (root / "packets").mkdir(parents=True)
    shutil.copy(FIXTURES / "manifest.json", root / "manifest.json")
    shutil.copytree(FIXTURES / "packets" / "mini-arkit", root / "packets" / "mini-arkit")
    return root


def test_committed_capture_fixture_passes_the_manifest_guard(guard) -> None:
    assert guard(FIXTURES) == []


def test_missing_referenced_file_fails_by_name(guard, copied_fixtures: Path) -> None:
    (copied_fixtures / "packets" / "mini-arkit" / "keyframes" / "k00003.jpg").unlink()

    errors = guard(copied_fixtures)

    assert errors, "an incomplete copy must not pass the guard"
    joined = "\n".join(errors)
    assert "missing input" in joined
    assert "keyframes/k00003.jpg" in joined
    assert "packet.json /files/4" in joined


def test_absent_stream_fails_by_name(guard, copied_fixtures: Path) -> None:
    (copied_fixtures / "packets" / "mini-arkit" / "streams" / "imu_raw.csv.gz").unlink()

    errors = guard(copied_fixtures)

    joined = "\n".join(errors)
    assert "missing input" in joined
    assert "streams/imu_raw.csv.gz" in joined


def test_packet_file_not_named_by_the_manifest_fails_by_name(guard, copied_fixtures: Path) -> None:
    packet_path = copied_fixtures / "packets" / "mini-arkit" / "packet.json"
    packet = json.loads(packet_path.read_text())
    entry = dict(packet["files"][0])
    entry["path"] = "keyframes/k00005.jpg"
    entry["bytes"] = 5
    entry["sha256"] = "0" * 64
    packet["files"].append(entry)
    packet_path.write_text(json.dumps(packet, indent=2))

    errors = guard(copied_fixtures)

    joined = "\n".join(errors)
    assert "keyframes/k00005.jpg" in joined
    assert "does not name" in joined


def test_tampered_file_fails_by_name(guard, copied_fixtures: Path) -> None:
    target = copied_fixtures / "packets" / "mini-arkit" / "keyframes" / "k00001.features.bin"
    data = bytearray(target.read_bytes())
    data[0] ^= 0xFF
    target.write_bytes(bytes(data))

    errors = guard(copied_fixtures)

    joined = "\n".join(errors)
    assert "does not match" in joined
    assert "keyframes/k00001.features.bin" in joined
