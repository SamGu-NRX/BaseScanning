"""Published-contract gates on plan families.

The first REAL_API run showed the synthetic fixture refused at finalize:
the server validates ``arkit packets must include at least one keyframe``,
and the generator wrote none. The app never sends an arkit packet without
keyframes, so the fixture was unrepresentative. These tests pin the
published contract at loader level:

- the synthetic generator emits arkit keyframes whose image files are
  declared with the ``keyframe`` role and whose digests bind;
- the loader refuses an arkit packet without keyframes;
- the loader accepts the committed mini-arkit plan family and refuses a
  tampered file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hunter_local_ingest.fixturegen import build, write_plan
from hunter_local_ingest.gen_miniarkit import MINIARKIT_PACKET_ID
from hunter_local_ingest.gen_miniarkit import write_plan as write_miniarkit
from hunter_local_ingest.plan import PlanError, canonical_digest, load_plan

REPO_ROOT = Path(__file__).resolve().parents[3]
MINIARKIT_DIR = REPO_ROOT / "backend" / "data" / "fixtures" / "packets" / "mini-arkit"


def _packet(document: dict) -> dict:
    return json.loads(bytes.fromhex(document["plan"]["packet_hex"]).decode("utf-8"))


def _rewrite_packet(document: dict, packet: dict) -> dict:
    """Re-seal the document around a modified packet object."""
    packet_bytes = json.dumps(packet, sort_keys=True, separators=(",", ":")).encode("utf-8")
    document["plan"]["packet_hex"] = packet_bytes.hex()
    document["plan"]["packet_sha256"] = hashlib.sha256(packet_bytes).hexdigest()
    document["generator"]["sha256_of_plan"] = canonical_digest(document)
    return document


def _declared(document: dict) -> dict[str, dict]:
    return {entry["path"]: entry for entry in document["plan"]["files"]}


def test_synthetic_arkit_packet_has_declared_keyframes(tmp_path: Path) -> None:
    document = write_plan(tmp_path / "plan.json", seed=1)
    packet = _packet(document)
    assert packet["session"]["tier"] in ("arkit", "arkit_lidar")
    keyframes = packet["keyframes"]
    assert isinstance(keyframes, list) and keyframes, "arkit packets include keyframes"
    declared = _declared(document)
    for keyframe in keyframes:
        image = keyframe["img"]
        assert image in declared, f"keyframe img {image} is not a declared file"
        entry = declared[image]
        assert entry["role"] == "keyframe"
        content = bytes.fromhex(entry["content_hex"])
        assert entry["sha256"] == hashlib.sha256(content).hexdigest()


def test_synthetic_mesh_limit_stays_within_manifest_file_limit() -> None:
    mesh_limit = 59
    build(1, mesh_limit, 1)
    with pytest.raises(ValueError, match=f"1..{mesh_limit}"):
        build(1, mesh_limit + 1, 1)


def test_loader_refuses_arkit_packet_without_keyframes(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    document = write_plan(path, seed=1)
    packet = _packet(document)
    packet["keyframes"] = []
    _rewrite_packet(document, packet)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(PlanError, match="keyframes"):
        load_plan(path)


@pytest.mark.skipif(not MINIARKIT_DIR.exists(), reason="repo checkout required")
def test_miniarkit_plan_loads_and_binds_digests(tmp_path: Path) -> None:
    path = tmp_path / "miniarkit-plan.json"
    write_miniarkit(path, MINIARKIT_DIR)
    loaded = load_plan(path)
    assert loaded.plan.packet_id == MINIARKIT_PACKET_ID
    packet = json.loads(loaded.plan.packet.decode("utf-8"))
    assert packet["session"]["tier"] == "arkit"
    assert packet["keyframes"], "the committed packet carries keyframes"

    # A tampered file is refused: the loader recomputes every digest.
    document = json.loads(path.read_text(encoding="utf-8"))
    document["plan"]["files"][0]["content_hex"] = (
        bytes.fromhex(document["plan"]["files"][0]["content_hex"])[:-1] + b"\x00"
    ).hex()
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(PlanError):
        load_plan(path)


def test_loader_records_the_generator_family(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    write_plan(path, seed=1)
    assert load_plan(path).generator_name == "gen_synthetic_packet"


@pytest.mark.skipif(not MINIARKIT_DIR.exists(), reason="repo checkout required")
def test_miniarkit_loader_records_the_generator_family(tmp_path: Path) -> None:
    path = tmp_path / "miniarkit-plan.json"
    write_miniarkit(path, MINIARKIT_DIR)
    assert load_plan(path).generator_name == "gen_miniarkit_packet"


def test_loader_refuses_a_non_string_generator_name(tmp_path: Path) -> None:
    """A list where the generator name belongs raises PlanError, not TypeError."""
    path = tmp_path / "plan.json"
    document = write_plan(path, seed=1)
    for bad_name in ([], {}, 5, True):
        document["generator"]["name"] = bad_name
        document["generator"]["sha256_of_plan"] = canonical_digest(document)
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with pytest.raises(PlanError, match="generator.name"):
            load_plan(path)


def test_loader_refuses_a_non_object_session(tmp_path: Path) -> None:
    """A string or list session raises PlanError, not AttributeError."""
    path = tmp_path / "plan.json"
    document = write_plan(path, seed=1)
    packet = _packet(document)
    for bad_session in ("x", ["arkit"], 3):
        packet["session"] = bad_session
        _rewrite_packet(document, packet)
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with pytest.raises(PlanError, match="session"):
            load_plan(path)


@pytest.mark.skipif(not MINIARKIT_DIR.exists(), reason="repo checkout required")
def test_runner_manifest_records_the_loaded_generator(tmp_path: Path) -> None:
    """The manifest names the generator that produced the run's fixture."""
    import argparse

    from hunter_local_ingest import runner

    path = tmp_path / "miniarkit-plan.json"
    write_miniarkit(path, MINIARKIT_DIR)
    loaded = load_plan(path)
    args = argparse.Namespace(
        endpoint="http://127.0.0.1:8000/v1",
        backend_mode="REAL_API",
        backend_ref=None,
        client_ref=None,
        restart_argv_json=None,
        restart_timeout_s=120.0,
        result_budget_s=1500.0,
        poll_interval_s=2.0,
        events_wait_s=20,
        max_retries=2,
        retry_backoff_s=1.0,
        with_faults=False,
    )
    manifest = runner._manifest(
        args, ["--endpoint", "http://127.0.0.1:8000/v1"], loaded, "0" * 16, "run"
    )
    assert manifest["fixture"]["generator"] == "gen_miniarkit_packet"
    assert manifest["fixture"]["seed"] is None


def test_runner_manifest_records_the_synthetic_generator(tmp_path: Path) -> None:
    import argparse

    from hunter_local_ingest import runner

    path = tmp_path / "plan.json"
    write_plan(path, seed=1)
    loaded = load_plan(path)
    args = argparse.Namespace(
        endpoint="http://127.0.0.1:8000/v1",
        backend_mode="REAL_API",
        backend_ref=None,
        client_ref=None,
        restart_argv_json=None,
        restart_timeout_s=120.0,
        result_budget_s=1500.0,
        poll_interval_s=2.0,
        events_wait_s=20,
        max_retries=2,
        retry_backoff_s=1.0,
        with_faults=False,
    )
    manifest = runner._manifest(args, ["--endpoint", "127.0.0.1:8000"], loaded, "0" * 16, "run")
    assert manifest["fixture"]["generator"] == "gen_synthetic_packet"
    assert manifest["fixture"]["seed"] == 1
