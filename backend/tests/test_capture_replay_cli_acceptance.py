"""End-to-end acceptance of the replay CLI against the supported packet.

Runs the actual ``scripts/capture_placement_cases.py`` CLI as a subprocess from a
temporary cwd, with a fresh work directory and state, over the default supported
``capture_cases_v1`` manifest: both positive captures solve via the real
conversion, merge and placement path, every declared negative refuses with no
placement artifact, and the receipt's cross-checks all hold. A deliberately
corrupted fixture and an unexpected positive refusal both exit nonzero; the
legacy W7 cases manifest stays available explicitly and keeps its own pinned
refusal state reported as blockers, never mistaken for acceptance.
"""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
RUNNER = BACKEND_ROOT / "scripts" / "capture_placement_cases.py"
PACKET_DIR = BACKEND_ROOT / "server/placement/tests/data/capture_cases_v1"
LEGACY_MANIFEST = BACKEND_ROOT / "server/placement/tests/data/cases/manifest.json"

POSITIVES = ("hazard-right-ground-gap", "hazard-left-ground-gap")
NEGATIVES = (
    "multiwall-source-unsupported",
    "generated-object-evidence-unsupported",
    "standoff-meter-anchor-unsupported",
    "footprint-hazard-unsupported",
    "generated-regions-unsupported",
)


def _run(root: Path, *extra: str) -> subprocess.CompletedProcess:
    """The real CLI, as a subprocess, from a temporary cwd with fresh state."""
    return subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--output",
            str(root / "receipt.json"),
            "--workdir",
            str(root / "work"),
            *extra,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _receipt(root: Path) -> dict:
    receipt = json.loads((root / "receipt.json").read_text())
    assert receipt["ok"] is True, receipt["blockers"]
    return receipt


def test_the_supported_packet_solves_end_to_end_through_the_real_cli(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]
    receipt = _receipt(tmp_path)

    summary = receipt["summary"]
    assert summary == {"cases": 7, "solved": 2, "refused": 5, "refusedDeclared": 5, "mismatched": 0, "errored": 0}
    assert len(receipt["codeSha"]) == 40

    by_id = {case["id"]: case for case in receipt["cases"]}
    for positive in POSITIVES:
        case = by_id[positive]
        assert case["status"] == "solved"
        assert case["mismatches"] == []
        # Real conversion against the independently authored wire, field by field.
        assert case["geometryComparison"] == "matched"
        # Input-hash binding on both solver paths, direct endpoint parity.
        assert case["stageInputShaMatchesCapturedBytes"] is True
        assert case["directInputShaMatchesPostedBytes"] is True
        assert case["directComparison"] == "matched"
        assert case["wireSemanticComparison"] == "matched"
        # Device and solver decisions, in distinct fields, both conservative.
        assert case["solverDecision"] == "manual_review"
        assert case["solverPolicy"]["id"] == "demo"
        assert case["solverPolicy"]["auto_approve"] is True
        assert case["deviceOutcome"]["captureStatus"] == "manual_review"
        assert case["deviceOutcome"]["servedResultKind"] == "manual_review"
        assert case["deviceOutcome"]["servedResultRunId"]
        assert case["terminalEventCount"] == 1
        assert case["hasPlacementArtifact"] is True
        for key in ("sourceSha256", "convertedSha256", "resultSha256"):
            assert len(case[key]) == 64
    # One-time tap replacement: the tap hazard survives the real merge and the
    # real conversion exactly once.
    tap = by_id["hazard-right-ground-gap"]
    assert tap["tapHazardMergeCount"] == 1
    assert tap["tapHazardConvertedCount"] == 1

    for negative in NEGATIVES:
        case = by_id[negative]
        assert case["status"] == "refused"
        assert case["hasPlacementArtifact"] is False
        refusal = case["refusal"]
        assert refusal["declared"] is True
        assert refusal["codeMatchesDeclaration"] is True
        assert refusal["pointerMatchesDeclaration"] is True
        assert case["deviceOutcome"]["servedResultKind"] == "manual_review"
        assert case["terminalEventCount"] == 1

    cross = receipt["crossCapture"]
    assert cross["distinctScenes"] is True
    assert cross["distinctNormalizedGeometry"] is True
    assert receipt["finalizeReplay"]["ok"] is True

    producers = receipt["producers"]
    for producer in ("base", "caseIntegrityFix", "replayAcceptance", "supportedCasePairs"):
        assert producers[producer]


def test_the_replay_cli_exits_nonzero_after_deliberate_fixture_corruption(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PACKET_DIR, packet)
    source = packet / "hazard-left-ground-gap.source.json"
    document = json.loads(source.read_text())
    document["objects"][0]["plus_minus_m"] = 0.5
    source.write_text(json.dumps(document, indent=2) + "\n")
    # The manifest's pinned sha256 is deliberately left behind: the drift is the
    # corruption, and the run must refuse to build on drifted evidence.

    result = _run(tmp_path, "--manifest", str(packet / "manifest.json"))
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "drifted fixture" in output and "!= pinned" in output


def test_the_replay_cli_exits_nonzero_when_a_positive_unexpectedly_refuses(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    shutil.copytree(PACKET_DIR, packet)
    source = packet / "hazard-left-ground-gap.source.json"
    document = json.loads(source.read_text())
    # The W7 refusal shape: a stand-off meter anchor the bridge does not support.
    document["objects"][0]["geometry"]["standoff_m"] = 0.1
    source.write_text(json.dumps(document, indent=2) + "\n")
    manifest_path = packet / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    pinned_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    for case in manifest["cases"]:
        if case["id"] == "hazard-left-ground-gap":
            case["source"]["sha256"] = pinned_sha
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    result = _run(tmp_path, "--manifest", str(manifest_path))
    assert result.returncode != 0
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    assert receipt["ok"] is False
    assert any("hazard-left-ground-gap" in blocker and "refused" in blocker for blocker in receipt["blockers"])


def test_the_legacy_cases_manifest_stays_available_explicitly(tmp_path: Path) -> None:
    result = _run(tmp_path, "--manifest", str(LEGACY_MANIFEST))
    assert result.returncode != 0
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    # The legacy manifest's pinned refusal state is reported as blockers — the
    # original limitation, not an acceptance criterion.
    assert receipt["ok"] is False
    assert receipt["summary"]["solved"] == 0
    assert receipt["blockers"]


@pytest.mark.parametrize("path", [RUNNER, PACKET_DIR / "manifest.json", LEGACY_MANIFEST])
def test_the_replay_targets_exist(path: Path) -> None:
    assert path.exists()
