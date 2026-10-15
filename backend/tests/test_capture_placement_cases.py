"""Focused tests for the capture placement case runner.

The runner (`scripts/capture_placement_cases.py`) replays every hash-pinned case
in `server/placement/tests/data/cases/manifest.json` through the real capture
front door — create, register, signed upload, commit, finalize, events, result —
over the documented dev settings (SD-73), injecting each case's synthetic source
scene **only** at the upstream `scene` stage boundary and an explicit object list
at the `objects_3d` boundary, with the actual capture/run/packet identity stamped
in the contract's identity fields.

These tests pin the three claims that make the runner worth trusting:

1. The journey evidence: the coordinator's merged scene equals the case's
   stamped source merged once with the injected objects (never a reference
   scene, another capture's scene, or a second merge); the observed tap hazard
   lands exactly once; refusals are typed, leave no placement artifact, and end
   in the conservative device outcome with exactly one terminal event; two
   captures in one app carry distinct scenes; a finalize replay is idempotent.
2. The suite fails loudly on tampering: substituting the old default stub's
   reference scene at the boundary, or removing the real placement stage, both
   produce mismatches and a nonzero exit — the boundary checks are load-bearing,
   not decorative.
3. The committed fixtures' pinned state (2026-10-15): both positive cases refuse
   conversion at `/objects/0/geometry/standoff_m` (a stand-off meter anchor the
   bridge does not synthesise, then the footprint hazard kind) with no expected
   error declared in the manifest, and `generated-hazard-unsupported` refuses at
   `/generated_regions` rather than its declared `/objects/1/evidence`. The
   suite reports these as blockers and exits nonzero: a producer-contract
   mismatch, recorded here as evidence rather than papered over.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import capture_placement_cases as runner  # noqa: E402

from server.eval.sceneio import validate  # noqa: E402
from server.orchestrator.stubs._common import write_files  # noqa: E402

MANIFEST = BACKEND / "server" / "placement" / "tests" / "data" / "cases" / "manifest.json"

POSITIVE_IDS = ("hazard-right-ground-gap", "hazard-left-ground-gap")
NEGATIVE_IDS = ("multiwall-source-unsupported", "generated-hazard-unsupported")


@pytest.fixture
def receipt(tmp_path, monkeypatch):
    """The full suite's receipt from a temporary working directory."""
    monkeypatch.setattr(sys, "argv", ["capture_placement_cases.py"])
    return runner.run_cases(MANIFEST, tmp_path / "run")


def _case(receipt: dict, case_id: str) -> dict:
    return next(case for case in receipt["cases"] if case["id"] == case_id)


def test_every_case_walks_the_real_front_door_with_case_true_boundaries(receipt):
    """Each case's journey: injected boundaries hold, refusal is typed and leaves
    no placement artifact, the device stays conservative, one terminal event."""
    assert receipt["schemaVersion"] == runner.RECEIPT_SCHEMA_VERSION
    summary = receipt["summary"]
    assert summary["cases"] == 4
    assert summary["errored"] == 0
    assert summary["solved"] == 0 and summary["refused"] == 4 and summary["refusedDeclared"] == 2

    for case in receipt["cases"]:
        assert case["runIdDeterministic"] is True
        assert case["sceneBoundaryMatchesCase"] is True, (
            "the merged scene is not this case's stamped source merged once"
        )
        assert case["objects3dStage"] == {"status": "done"}
        assert case["hasPlacementArtifact"] is False, "a refused placement must not publish an artifact"
        assert case["status"] == "refused"
        assert case["refusal"]["code"] == "placement_scene_unsupported"
        assert case["deviceOutcome"] == {
            "captureStatus": "manual_review",
            "servedResultHttpStatus": 200,
            "servedResultKind": "manual_review",
        }
        assert case["terminalEventCount"] == 1

    # The one nonempty objects_3d case: the observed tap hazard, merged exactly once.
    assert _case(receipt, runner.TAP_CASE_ID)["tapHazardMergeCount"] == 1

    # Two distinct captures in one app: no cross-capture scene reuse.
    assert receipt["crossCapture"]["distinctScenes"] is True

    # A finalize replay is idempotent: same run id, still one terminal event.
    assert receipt["finalizeReplay"]["ok"] is True
    assert receipt["finalizeReplay"]["terminalEventCountAfter"] == 1

    # The receipt never carries the token or any signed upload capability.
    assert runner.TOKEN not in json.dumps(receipt)


def test_declared_refusals_match_their_declarations(receipt):
    """Negative sources must yield exactly their declared typed refusal."""
    multiwall = _case(receipt, "multiwall-source-unsupported")
    assert multiwall["refusal"]["declared"] is True
    assert multiwall["refusal"]["codeMatchesDeclaration"] is True
    assert multiwall["refusal"]["pointerMatchesDeclaration"] is True
    assert multiwall["mismatches"] == []

    # Pinned producer-contract mismatch: the bridge refuses the generated regions
    # themselves before it could ever reach the declared /objects/1/evidence.
    generated = _case(receipt, "generated-hazard-unsupported")
    assert generated["refusal"]["codeMatchesDeclaration"] is True
    assert generated["refusal"]["pointerMatchesDeclaration"] is False
    assert "/generated_regions" in generated["refusal"]["message"]


def test_committed_positive_sources_do_not_convert(receipt):
    """The positive pairs' sources are refused by the bridge as authored — the
    pinned blocker this suite reports instead of papering over."""
    for case_id in POSITIVE_IDS:
        refusal = _case(receipt, case_id)["refusal"]
        assert refusal["declared"] is False
        assert "/objects/0/geometry/standoff_m" in refusal["message"], (
            "the meter anchor's stand-off is the first refusal the bridge raises"
        )
        assert _case(receipt, case_id)["mismatches"], "an undeclared positive refusal must be a suite blocker"
    assert receipt["blockers"], "the fixture state above must make the suite fail loudly"
    assert receipt["ok"] is False


def test_substituting_a_reference_scene_fails_the_suite(tmp_path, monkeypatch):
    """Swapping the boundary back to a reference scene is caught by the merged-scene comparison."""

    def substituted_scene_run(ctx):
        entry = runner._REGISTRY.get(ctx.capture_id)
        if entry is None:
            raise RuntimeError(f"no injected source scene for {ctx.capture_id}")
        # A plausible-looking reference scene: valid, but not this case's source.
        scene = {
            "format": "base-scene/v0",
            "scene_id": f"reference-substitution+{ctx.capture_id}",
            "units": {"length": "m"},
            "wall": {"kind": "wall_rect", "a_m": [-2.0, 0.0], "b_m": [2.0, 0.0], "height_m": 2.4},
            "objects": [],
            "frame": {"anchor": {"element_id": "meter", "azimuth_hint": "front"}},
            "provenance": {
                "data": "reference",
                "site": "test",
                "producer": "runner-test",
                "scale_source": "none",
                "gravity_source": "none",
            },
        }
        validate(scene)
        return write_files(ctx, {"scene/scene.json": json.dumps(scene, sort_keys=True).encode()})

    monkeypatch.setattr(runner, "_injected_scene_run", substituted_scene_run)
    receipt = runner.run_cases(MANIFEST, tmp_path / "run", case_ids=["multiwall-source-unsupported"])
    case = receipt["cases"][0]
    assert case["sceneBoundaryMatchesCase"] is False
    assert any("merged scene" in mismatch for mismatch in case["mismatches"])
    assert receipt["ok"] is False


def test_removing_the_real_placement_stage_fails_the_suite(tmp_path, monkeypatch):
    """The declared refusals must come from the real bridge behind the stage: with
    the stage's entry point replaced, the negative case no longer refuses with
    its declared code, and the suite fails."""

    class Removed(Exception):
        code = "placement_boundary_missing"

        def __init__(self, *args, **kwargs):
            pass

    def removed_run(*args, **kwargs):
        raise Removed()

    adapter = importlib.import_module("server.placement")
    monkeypatch.setattr(adapter, "run", removed_run)
    receipt = runner.run_cases(MANIFEST, tmp_path / "run", case_ids=["multiwall-source-unsupported"])
    case = receipt["cases"][0]
    assert case["refusal"]["code"] == "placement_boundary_missing"
    assert case["refusal"]["codeMatchesDeclaration"] is False
    assert receipt["ok"] is False


def test_cli_runs_from_a_temporary_directory_and_exits_nonzero_while_blocked(tmp_path):
    """The one-command entry point: receipt on disk, nonzero exit while the
    committed fixtures stay bridge-incompatible, no secrets in the file."""
    output = tmp_path / "receipt.json"
    exit_code = runner.main(["--output", str(output), "--workdir", str(tmp_path / "run")])
    assert exit_code == 1
    receipt = json.loads(output.read_text())
    assert receipt["schemaVersion"] == runner.RECEIPT_SCHEMA_VERSION
    assert receipt["summary"]["cases"] == 4
    assert runner.TOKEN not in output.read_text()
    blockers = "\n".join(receipt["blockers"])
    assert "standoff_m" in blockers and "/objects/1/evidence" in blockers
