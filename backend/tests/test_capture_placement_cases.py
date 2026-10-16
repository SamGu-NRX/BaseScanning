"""Acceptance tests for the capture placement replay runner.

The committed manifest's positive sources refuse conversion (the pinned blocker),
so the success path is exercised through a temporary manifest whose positive
sources, wire scenes, and expectations are authored by hand in this module — the
same geometry in metres and in feet, never produced by running the converter.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import capture_placement_cases as runner  # noqa: E402

from server.eval.sceneio import validate  # noqa: E402
from server.orchestrator.stubs._common import json_bytes, write_files  # noqa: E402

MANIFEST = BACKEND / "server" / "placement" / "tests" / "data" / "cases" / "manifest.json"

POSITIVE_IDS = ("hazard-right-ground-gap", "hazard-left-ground-gap")
NEGATIVE_IDS = ("multiwall-source-unsupported", "generated-hazard-unsupported")

M_PER_FT = 0.3048


def _ft(metres: float) -> float:
    """Feet for a source metres value — the conversion the bridge must apply,
    used here to author the wire scenes by hand, not by running the converter."""
    return metres / M_PER_FT


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def receipt(tmp_path, monkeypatch):
    """The full suite's receipt from a temporary working directory."""
    monkeypatch.setattr(sys, "argv", ["capture_placement_cases.py"])
    return runner.run_cases(MANIFEST, tmp_path / "run")


def _case(receipt: dict, case_id: str) -> dict:
    return next(case for case in receipt["cases"] if case["id"] == case_id)


# ---------------------------------------------------------------------------
# Hand-authored supported sources, wire scenes, and pinned expectations
# ---------------------------------------------------------------------------


def _supported_source_a() -> dict:
    """A hand-authored positive source: one wall, a meter anchor point, a window,
    a tap gas hazard, an observed ground pad, overhead, facing gap, coverage.
    The solver has plausible spots but unsure evidence forces manual review."""
    return {
        "format": "base-scene/v0",
        "scene_id": "burst8-supported-a",
        "provenance": {
            "data": "synthetic",
            "site": "burst8-supported-a",
            "producer": {"path": "capture-placement-cases-test"},
            "scale_source": "native_metric",
            "gravity_source": "ground_truth",
        },
        "frame": {
            "units": "m",
            "convention": "meter_anchored/v0",
            "anchor": {"element_id": "meter", "plus_minus_m": 0.01},
        },
        "walls": [
            {
                "id": "side",
                "role": "house",
                "baseline_m": [[-3.0, 0.0], [5.0, 0.0]],
                "height_m": 3.0,
                "plus_minus_m": 0.05,
                "evidence": "observed",
            }
        ],
        "chain": {
            "wall_ids": ["side"],
            "ends": {"left": {"kind": "unexplored"}, "right": {"kind": "limit", "note": "fence line"}},
        },
        "ground": {
            "surface": {"kind": "plane", "normal": [0, 1, 0], "offset_m": 0},
            "plus_minus_m": 0.05,
            "regions": [
                {
                    "id": "pad",
                    "material": "concrete",
                    "polygon_m": [[0.2, 0.5], [2.0, 0.5], [2.0, 3.0], [0.2, 3.0]],
                    "evidence": "observed",
                    "plus_minus_m": 0.1,
                }
            ],
        },
        "objects": [
            {
                "id": "meter",
                "class": "meter",
                "geometry": {"kind": "point", "pos_m": [0.0, 1.5, 0.02]},
                "confidence": 1,
                "plus_minus_m": 0.02,
                "source": {"kind": "reconstruction"},
                "evidence": "observed",
            },
            {
                "id": "win",
                "class": "window",
                "geometry": {"kind": "wall_rect", "wall_id": "side", "s_m": [1.0, 2.0], "bottom_m": 1.0, "top_m": 2.2},
                "confidence": 0.9,
                "plus_minus_m": 0.08,
                "source": {"kind": "vlm"},
                "evidence": "observed",
            },
            {
                "id": "gas",
                "class": "gas_meter",
                "geometry": {
                    "kind": "wall_rect",
                    "wall_id": "side",
                    "s_m": [-0.6, -0.3],
                    "bottom_m": 0.3,
                    "top_m": 0.9,
                },
                "confidence": 0.95,
                "plus_minus_m": 0.05,
                "source": {"kind": "tap"},
                "evidence": "observed",
                "attrs": {"operable": True},
            },
        ],
        "coverage": {
            "method": "capture-placement-cases-test",
            "bands": [{"band": "wall", "s_m": [-2.0, 4.0], "out_m": 2.13}],
            "sensitivity": [
                {
                    "surface": "wall",
                    "max_incidence_deg": 75,
                    "bands": [{"band": "wall", "s_m": [-2.0, 4.0], "out_m": 2.13}],
                }
            ],
        },
        "overheads": [
            {"wall_id": "side", "s_m": [1.0, 2.0], "clearance_m": 2.1, "plus_minus_m": 0.1, "evidence": "observed"}
        ],
        "facing": [
            {"wall_id": "side", "s_m": [-1.0, 1.0], "depth_m": 8.0, "plus_minus_m": 0.3, "evidence": "observed"}
        ],
    }


def _supported_source_b() -> dict:
    """A second, mirrored positive source: the gas hazard right of the meter, the
    window left, observed ground on the left, the left end a real limit and the
    right end unexplored — the opposite of source A on every one of those."""
    return {
        "format": "base-scene/v0",
        "scene_id": "burst8-supported-b",
        "provenance": {
            "data": "synthetic",
            "site": "burst8-supported-b",
            "producer": {"path": "capture-placement-cases-test"},
            "scale_source": "native_metric",
            "gravity_source": "ground_truth",
        },
        "frame": {
            "units": "m",
            "convention": "meter_anchored/v0",
            "anchor": {"element_id": "meter", "plus_minus_m": 0.01},
        },
        "walls": [
            {
                "id": "side",
                "role": "house",
                "baseline_m": [[-5.0, 0.0], [3.0, 0.0]],
                "height_m": 2.8,
                "plus_minus_m": 0.06,
                "evidence": "observed",
            }
        ],
        "chain": {
            "wall_ids": ["side"],
            "ends": {"left": {"kind": "limit", "note": "hedge line"}, "right": {"kind": "unexplored"}},
        },
        "ground": {
            "surface": {"kind": "plane", "normal": [0, 1, 0], "offset_m": 0},
            "plus_minus_m": 0.05,
            "regions": [
                {
                    "id": "pad",
                    "material": "gravel",
                    "polygon_m": [[-2.0, 0.5], [-0.6, 0.5], [-0.6, 3.0], [-2.0, 3.0]],
                    "evidence": "observed",
                    "plus_minus_m": 0.08,
                }
            ],
        },
        "objects": [
            {
                "id": "meter",
                "class": "meter",
                "geometry": {"kind": "point", "pos_m": [0.0, 1.4, 0.03]},
                "confidence": 1,
                "plus_minus_m": 0.03,
                "source": {"kind": "reconstruction"},
                "evidence": "observed",
            },
            {
                "id": "win",
                "class": "window",
                "geometry": {
                    "kind": "wall_rect",
                    "wall_id": "side",
                    "s_m": [-2.4, -1.4],
                    "bottom_m": 1.0,
                    "top_m": 2.0,
                },
                "confidence": 0.9,
                "plus_minus_m": 0.08,
                "source": {"kind": "vlm"},
                "evidence": "observed",
            },
            {
                "id": "gas",
                "class": "gas_meter",
                "geometry": {
                    "kind": "wall_rect",
                    "wall_id": "side",
                    "s_m": [0.8, 1.3],
                    "bottom_m": 0.25,
                    "top_m": 0.85,
                },
                "confidence": 0.95,
                "plus_minus_m": 0.05,
                "source": {"kind": "tap"},
                "evidence": "observed",
            },
        ],
        "coverage": {
            "method": "capture-placement-cases-test",
            "bands": [{"band": "wall", "s_m": [-4.0, 2.0], "out_m": 2.5}],
            "sensitivity": [
                {
                    "surface": "wall",
                    "max_incidence_deg": 75,
                    "bands": [{"band": "wall", "s_m": [-4.0, 2.0], "out_m": 2.5}],
                }
            ],
        },
        "overheads": [
            {"wall_id": "side", "s_m": [-2.4, -1.4], "clearance_m": 2.0, "plus_minus_m": 0.12, "evidence": "observed"}
        ],
        "facing": [
            {"wall_id": "side", "s_m": [-1.5, 0.5], "depth_m": 7.0, "plus_minus_m": 0.25, "evidence": "observed"}
        ],
    }


def _wire_a(*, with_tap: bool = True) -> dict:
    """The wire scene a converter must produce for source A, authored from the
    source geometry with metres divided by 0.3048. The observed tap hazard is
    injected by the runner for the reserved positive id, so the wire scene
    includes it as a third object unless a caller excludes it."""
    wire = {
        "meter": {"pos": [0.0, _ft(1.5), _ft(0.02)], "wall_id": "side", "plus_minus_ft": _ft(0.02)},
        "walls": [
            {
                "id": "side",
                "baseline": [[_ft(-3.0), 0.0], [_ft(5.0), 0.0]],
                "height_ft": _ft(3.0),
                "plus_minus_ft": _ft(0.05),
            }
        ],
        "objects": [
            {
                "type": "window",
                "wall_id": "side",
                "span_ft": [_ft(1.0), _ft(2.0)],
                "bottom_ft": _ft(1.0),
                "top_ft": _ft(2.2),
                "conf": 0.9,
                "plus_minus_ft": _ft(0.08),
                "source": "vlm",
            },
            {
                "type": "gas_meter",
                "wall_id": "side",
                "span_ft": [_ft(-0.6), _ft(-0.3)],
                "bottom_ft": _ft(0.3),
                "top_ft": _ft(0.9),
                "conf": 0.95,
                "plus_minus_ft": _ft(0.05),
                "source": "tap",
                "attrs": {"operable": True},
            },
        ],
        "ground": [
            {
                "type": "concrete",
                "polygon": [
                    [_ft(0.2), _ft(0.5)],
                    [_ft(2.0), _ft(0.5)],
                    [_ft(2.0), _ft(3.0)],
                    [_ft(0.2), _ft(3.0)],
                ],
                "plus_minus_ft": _ft(0.1),
            }
        ],
        "overheads": [
            {"wall_id": "side", "span_ft": [_ft(1.0), _ft(2.0)], "clearance_ft": _ft(2.1), "plus_minus_ft": _ft(0.1)}
        ],
        "facing": [
            {"wall_id": "side", "span_ft": [_ft(-1.0), _ft(1.0)], "depth_ft": _ft(8.0), "plus_minus_ft": _ft(0.3)}
        ],
        "coverage": {
            "ends": {"left": {"kind": "unexplored"}, "right": {"kind": "limit", "note": "fence line"}},
            "observed": [{"band": "wall", "span_ft": [_ft(-2.0), _ft(4.0)], "out_ft": _ft(2.13)}],
        },
    }
    if with_tap:
        wire["objects"].append(
            {
                "type": "vent",
                "wall_id": "side",
                "span_ft": [_ft(1.6), _ft(1.8)],
                "bottom_ft": _ft(0.5),
                "top_ft": _ft(2.0),
                "conf": 0.9,
                "plus_minus_ft": _ft(0.05),
                "source": "tap",
            }
        )
    return wire


def _wire_b() -> dict:
    """The wire scene for source B, authored the same way — from the metres."""
    return {
        "meter": {"pos": [0.0, _ft(1.4), _ft(0.03)], "wall_id": "side", "plus_minus_ft": _ft(0.03)},
        "walls": [
            {
                "id": "side",
                "baseline": [[_ft(-5.0), 0.0], [_ft(3.0), 0.0]],
                "height_ft": _ft(2.8),
                "plus_minus_ft": _ft(0.06),
            }
        ],
        "objects": [
            {
                "type": "window",
                "wall_id": "side",
                "span_ft": [_ft(-2.4), _ft(-1.4)],
                "bottom_ft": _ft(1.0),
                "top_ft": _ft(2.0),
                "conf": 0.9,
                "plus_minus_ft": _ft(0.08),
                "source": "vlm",
            },
            {
                "type": "gas_meter",
                "wall_id": "side",
                "span_ft": [_ft(0.8), _ft(1.3)],
                "bottom_ft": _ft(0.25),
                "top_ft": _ft(0.85),
                "conf": 0.95,
                "plus_minus_ft": _ft(0.05),
                "source": "tap",
            },
        ],
        "ground": [
            {
                "type": "gravel",
                "polygon": [
                    [_ft(-2.0), _ft(0.5)],
                    [_ft(-0.6), _ft(0.5)],
                    [_ft(-0.6), _ft(3.0)],
                    [_ft(-2.0), _ft(3.0)],
                ],
                "plus_minus_ft": _ft(0.08),
            }
        ],
        "overheads": [
            {"wall_id": "side", "span_ft": [_ft(-2.4), _ft(-1.4)], "clearance_ft": _ft(2.0), "plus_minus_ft": _ft(0.12)}
        ],
        "facing": [
            {"wall_id": "side", "span_ft": [_ft(-1.5), _ft(0.5)], "depth_ft": _ft(7.0), "plus_minus_ft": _ft(0.25)}
        ],
        "coverage": {
            "ends": {"left": {"kind": "limit", "note": "hedge line"}, "right": {"kind": "unexplored"}},
            "observed": [{"band": "wall", "span_ft": [_ft(-4.0), _ft(2.0)], "out_ft": _ft(2.5)}],
        },
    }


def _expected_a() -> dict:
    """Geometry facts, not policy: pinned from the solver's deterministic output
    on exactly the merged scene this manifest produces (tap hazard included)."""
    return {
        "decision": "manual_review",
        "spot": {
            "wall_id": "side",
            "segment": 0,
            "span_ft": [9.56168, 12.145013],
            "outcome": "unsure",
        },
        "checks": {
            "gas_clearance": {"outcome": "unsure", "unsure_cause": "unobserved"},
            "ground_surface": {"outcome": "unsure", "unsure_cause": "unobserved"},
            "opening_clearance": {"outcome": "unsure", "unsure_cause": "margin"},
            "wall_equipment_above": {"outcome": "pass", "unsure_cause": None},
            "route_length": {"outcome": "pass", "unsure_cause": None},
        },
        "missing_evidence": [
            {"kind": "band", "band": "ground", "span_ft": [-0.614621, 22.321313], "out_ft": 12.009634},
            {"kind": "past_end", "side": "left", "span_ft": [-9.84252, -9.84252]},
        ],
        "ends": {"left": "unexplored", "right": "limit"},
    }


def _expected_b() -> dict:
    return {
        "decision": "manual_review",
        "spot": {
            "wall_id": "side",
            "segment": 0,
            "span_ft": [-13.457349, -10.874016],
            "outcome": "unsure",
        },
        "checks": {
            "ground_surface": {"outcome": "unsure", "unsure_cause": "unobserved"},
            "gas_clearance": {"outcome": "unsure", "unsure_cause": "unobserved"},
            "opening_clearance": {"outcome": "unsure", "unsure_cause": "margin"},
            "meter_working_space": {"outcome": "pass", "unsure_cause": None},
            "wall_equipment_above": {"outcome": "unsure", "unsure_cause": "unobserved"},
            "route_length": {"outcome": "pass", "unsure_cause": None},
        },
        "missing_evidence": [
            {"kind": "band", "band": "ground", "span_ft": [-23.666497, -0.664867], "out_ft": 12.042482},
            {"kind": "past_end", "side": "right", "span_ft": [9.84252, 9.84252]},
        ],
        "ends": {"left": "limit", "right": "unexplored"},
    }


def _write_supported_manifest(tmp_path: Path) -> Path:
    """A temp manifest: two hand-authored positive cases (source, wire scene and
    expectations written and sha-pinned like the committed fixtures) plus one
    committed negative case referenced by absolute path. This manifest is the
    one command's success path: both positives must solve and publish."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir(parents=True)
    cases = []
    for case_id, source, wire, expected in (
        (runner.TAP_CASE_ID, _supported_source_a(), _wire_a(), _expected_a()),
        ("hazard-left-wall-gap", _supported_source_b(), _wire_b(), _expected_b()),
    ):
        source_path = fixtures / f"{case_id}.source.json"
        source_path.write_text(json.dumps(source, indent=1) + "\n")
        wire_path = fixtures / f"{case_id}.wire.json"
        wire_path.write_text(json.dumps(wire, indent=1, sort_keys=True) + "\n")
        cases.append(
            {
                "id": case_id,
                "kind": "positive",
                "source": {"path": str(source_path), "sha256": _sha256_file(source_path)},
                "wire": {"path": str(wire_path), "sha256": _sha256_file(wire_path)},
                "expectations": expected,
                "note": "Hand-authored pair: the same geometry in metres and in feet.",
            }
        )
    cases.append(
        {
            "id": "multiwall-source-unsupported",
            "kind": "negative_source",
            "source": {
                "path": str(BACKEND / "server/placement/tests/data/cases/multiwall-source-unsupported.source.json"),
                "sha256": _sha256_file(
                    BACKEND / "server/placement/tests/data/cases/multiwall-source-unsupported.source.json"
                ),
            },
            "wire": None,
            "expected_error": {"code": "placement_scene_unsupported", "pointer": "/walls"},
        }
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "placement-case-manifest/v0",
                "description": ["Temporary supported-source manifest for the runner acceptance tests."],
                "cases": cases,
            },
            indent=1,
        )
        + "\n"
    )
    return manifest_path


def _write_twin_manifest(tmp_path: Path) -> Path:
    """Two positive cases sharing one source and one wire scene byte for byte:
    different identity stamps only. Cross-capture must fail them on geometry."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir(parents=True)
    source_path = fixtures / "twin.source.json"
    source_path.write_text(json.dumps(_supported_source_a(), indent=1) + "\n")
    wire_path = fixtures / "twin.wire.json"
    wire_path.write_text(json.dumps(_wire_a(with_tap=False), indent=1, sort_keys=True) + "\n")
    source_ref = {"path": str(source_path), "sha256": _sha256_file(source_path)}
    wire_ref = {"path": str(wire_path), "sha256": _sha256_file(wire_path)}
    cases = [
        {
            "id": "twin-one",
            "kind": "positive",
            "source": source_ref,
            "wire": wire_ref,
            # The hardened loader (case-integrity fix) rejects decision-only
            # positives, so the twins pin the coverage chain ends too — the
            # cross-capture refusal must still come from geometry, not the loader.
            "expectations": {"decision": "manual_review", "ends": {"left": "unexplored", "right": "limit"}},
        },
        {
            "id": "twin-two",
            "kind": "positive",
            "source": source_ref,
            "wire": wire_ref,
            "expectations": {"decision": "manual_review", "ends": {"left": "unexplored", "right": "limit"}},
        },
    ]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "placement-case-manifest/v0",
                "description": ["Twin-identity manifest: one geometry, two identities."],
                "cases": cases,
            },
            indent=1,
        )
        + "\n"
    )
    return manifest_path


# ---------------------------------------------------------------------------
# The committed manifest: refusal state, pinned and loud
# ---------------------------------------------------------------------------


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
        assert case["deviceOutcome"]["captureStatus"] == "manual_review"
        assert case["deviceOutcome"]["servedResultHttpStatus"] == 200
        assert case["deviceOutcome"]["servedResultKind"] == "manual_review"
        assert case["deviceOutcome"]["servedResultRunId"] == case["runId"], (
            "the served result must carry this journey's own run id"
        )
        assert case["terminalEventCount"] == 1

    # The one nonempty objects_3d case: the observed tap hazard, merged exactly once.
    assert _case(receipt, runner.TAP_CASE_ID)["tapHazardMergeCount"] == 1

    # Both positives refused: no merged scenes, so the cross-capture comparison
    # cannot run and must say so instead of passing vacuously.
    cross = receipt["crossCapture"]
    assert cross["status"] == "not_applicable"
    assert cross["distinctScenes"] is None and cross["distinctNormalizedGeometry"] is None
    assert cross["captureIds"] == [
        _case(receipt, "hazard-right-ground-gap")["captureId"],
        _case(receipt, "hazard-left-ground-gap")["captureId"],
    ]

    # A finalize replay is idempotent: same run id, still one terminal event.
    assert receipt["finalizeReplay"]["ok"] is True
    assert receipt["finalizeReplay"]["terminalEventCountAfter"] == 1
    assert receipt["finalizeReplay"]["applicable"] is True

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
    pinned blocker this suite reports instead of papering over. A declared or
    undeclared positive refusal is a blocker either way: only a positive case
    that solves counts as success."""
    for case_id in POSITIVE_IDS:
        refusal = _case(receipt, case_id)["refusal"]
        assert refusal["declared"] is False
        assert "/objects/0/geometry/standoff_m" in refusal["message"], (
            "the meter anchor's stand-off is the first refusal the bridge raises"
        )
        assert _case(receipt, case_id)["mismatches"], "an undeclared positive refusal must be a suite blocker"
    blockers = "\n".join(receipt["blockers"])
    assert "full run requires both positive cases" in blockers
    assert "hazard-right-ground-gap" in blockers and "hazard-left-ground-gap" in blockers
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
    # The committed legacy manifest, passed explicitly since the CLI's default
    # moved to the supported packet (its refusal state stays pinned here).
    exit_code = runner.main(
        [
            "--manifest",
            str(runner.case_suite.DEFAULT_MANIFEST),
            "--output",
            str(output),
            "--workdir",
            str(tmp_path / "run"),
        ]
    )
    assert exit_code == 1
    receipt = json.loads(output.read_text())
    assert receipt["schemaVersion"] == runner.RECEIPT_SCHEMA_VERSION
    assert receipt["summary"]["cases"] == 4
    assert runner.TOKEN not in output.read_text()
    blockers = "\n".join(receipt["blockers"])
    assert "standoff_m" in blockers and "/objects/1/evidence" in blockers


# ---------------------------------------------------------------------------
# The supported manifest: the success path and the faults that break it
# ---------------------------------------------------------------------------


def test_supported_sources_solve_end_to_end(tmp_path):
    """Hand-authored positive sources convert, solve, and publish: the converted
    bytes match the wire scene field by field, both solver responses bind their
    input hash to their own exact request bytes, the semantics match the pinned
    expectations, the device boundary stays conservative on the journey's own run
    id, and the two positives carry distinct normalized geometry."""
    manifest = _write_supported_manifest(tmp_path)
    receipt = runner.run_cases(manifest, tmp_path / "run")
    assert receipt["ok"] is True, receipt["blockers"]

    positives = [case for case in receipt["cases"] if case["kind"] == "positive"]
    assert len(positives) == 2
    for case in positives:
        assert case["status"] == "solved"
        assert case["sceneBoundaryMatchesCase"] is True
        assert case["hasPlacementArtifact"] is True
        assert case["stageInputShaMatchesCapturedBytes"] is True
        assert case["directInputShaMatchesPostedBytes"] is True
        assert case["directComparison"] == "matched"
        assert case["wireSemanticComparison"] == "matched", case["wireSemanticMismatches"]
        assert case["geometryComparison"] == "matched", case.get("geometryDiffs")
        assert case["unitsSentinel"] == "matched"
        assert case["deviceOutcome"]["servedResultHttpStatus"] == 200
        assert case["deviceOutcome"]["servedResultKind"] == "manual_review"
        assert case["deviceOutcome"]["servedResultRunId"] == case["runId"]
        assert case["terminalEventCount"] == 1
        assert case["mismatches"] == []

    # The tap hazard converted exactly once, at its expected feet span.
    assert _case(receipt, runner.TAP_CASE_ID)["tapHazardConvertedCount"] == 1

    cross = receipt["crossCapture"]
    assert cross["status"] == "evaluated"
    assert cross["distinctScenes"] is True
    assert cross["distinctNormalizedGeometry"] is True
    assert len(set(cross["captureIds"])) == 2
    assert receipt["finalizeReplay"]["ok"] is True

    negative = _case(receipt, "multiwall-source-unsupported")
    assert negative["status"] == "refused" and negative["mismatches"] == []


def test_cli_on_the_supported_manifest_exits_zero(tmp_path):
    """The one-command entry point on a manifest that can actually succeed."""
    manifest = _write_supported_manifest(tmp_path)
    output = tmp_path / "receipt.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(BACKEND / "scripts" / "capture_placement_cases.py"),
            "--manifest",
            str(manifest),
            "--output",
            str(output),
            "--workdir",
            str(tmp_path / "run"),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    receipt = json.loads(output.read_text())
    assert receipt["ok"] is True
    assert receipt["crossCapture"]["distinctNormalizedGeometry"] is True


def test_cli_on_the_committed_manifest_exits_nonzero_in_a_subprocess(tmp_path):
    """The failure path through the real argv/exit-code boundary too."""
    output = tmp_path / "receipt.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(BACKEND / "scripts" / "capture_placement_cases.py"),
            "--manifest",
            str(runner.case_suite.DEFAULT_MANIFEST),
            "--output",
            str(output),
            "--workdir",
            str(tmp_path / "run"),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=600,
    )
    assert proc.returncode == 1
    assert "standoff_m" in proc.stderr


def test_case_selection_cannot_pass_silently(tmp_path):
    """An empty selection and an unknown selection both refuse to run."""
    manifest = _write_supported_manifest(tmp_path)
    with pytest.raises(SystemExit):
        runner.run_cases(manifest, tmp_path / "run", case_ids=[])
    with pytest.raises(SystemExit):
        runner.run_cases(manifest, tmp_path / "run", case_ids=["no-such-case"])


def test_corrupting_the_converted_scene_fails_the_suite(tmp_path, monkeypatch):
    """A converter that silently moves the gas hazard is caught by the
    field-by-field comparison against the hand-authored wire scene — the staged
    response and the direct POST both agree with each other and both are wrong."""

    placement_adapter = importlib.import_module("server.placement.adapter")
    real_convert = placement_adapter.convert_scene

    def corrupting_convert(document):
        converted = real_convert(document)
        for obj in converted.get("objects", []):
            if obj.get("type") == "gas_meter":
                obj["span_ft"] = [obj["span_ft"][0] + 0.5, obj["span_ft"][1] + 0.5]
        return converted

    monkeypatch.setattr(placement_adapter, "convert_scene", corrupting_convert)
    receipt = runner.run_cases(_write_supported_manifest(tmp_path), tmp_path / "run", case_ids=[runner.TAP_CASE_ID])
    case = receipt["cases"][0]
    assert case["status"] == "solved"
    assert case["geometryComparison"] == "mismatched"
    assert any("wire scene" in mismatch for mismatch in case["mismatches"])
    assert receipt["ok"] is False


def test_forging_the_solver_input_hash_fails_the_suite(tmp_path, monkeypatch):
    """A solver that stamps the same forged hash into both replies keeps them in
    agreement with each other — and fails both hash bindings against the exact
    request bytes."""

    placement_adapter = importlib.import_module("server.placement.adapter")
    modules = placement_adapter.load_api()
    solver, api = modules["solver"], modules["api"]
    real_solve = solver.solve

    def forging_solve(parsed, rules):
        result = real_solve(parsed, rules)
        result["stats"]["input_sha256"] = "f" * 64
        return result

    # The stage resolves solver.solve dynamically; the API route binds the name
    # at import (`from solver import solve`). Patch both so both replies carry
    # the same forged hash and still agree with each other.
    monkeypatch.setattr(solver, "solve", forging_solve)
    monkeypatch.setattr(api, "solve", forging_solve)
    receipt = runner.run_cases(_write_supported_manifest(tmp_path), tmp_path / "run", case_ids=[runner.TAP_CASE_ID])
    case = receipt["cases"][0]
    assert case["status"] == "solved"
    assert case["stageInputShaMatchesCapturedBytes"] is False
    assert case["directInputShaMatchesPostedBytes"] is False
    assert case["directComparison"] == "matched", "the two forged responses must still agree with each other"
    assert any("input_sha256" in mismatch for mismatch in case["mismatches"])
    assert receipt["ok"] is False


@pytest.mark.parametrize("mutation,field", [("kind", "servedResultKind"), ("runId", "servedResultRunId")])
def test_a_served_result_that_lies_fails_the_suite(tmp_path, monkeypatch, mutation, field):
    """The device boundary is checked against what is actually served: a result
    endpoint that flips the outcome kind or the run id fails the suite."""

    real_client = runner.TestClient

    class LyingClient(real_client):
        def get(self, url, **kwargs):
            response = super().get(url, **kwargs)
            if str(url).endswith("/result") and response.status_code == 200:
                body = response.json()
                if mutation == "kind":
                    body["outcome"] = {"kind": "proposed", "reason_codes": ["forged"]}
                else:
                    body["runId"] = "run_forged"
                return httpx.Response(200, json=body)
            return response

    monkeypatch.setattr(runner, "TestClient", LyingClient)
    receipt = runner.run_cases(_write_supported_manifest(tmp_path), tmp_path / "run", case_ids=[runner.TAP_CASE_ID])
    case = receipt["cases"][0]
    assert case["deviceOutcome"][field] == ("proposed" if mutation == "kind" else "run_forged")
    assert case["mismatches"], "a lying served result must be a case mismatch"
    assert receipt["ok"] is False


def test_a_failed_finalize_replay_blocks_the_suite(tmp_path, monkeypatch):
    """A finalize replay that is no longer idempotent is an aggregate blocker."""

    def failed_replay(harness, journey):
        return {
            "caseId": journey["caseId"],
            "replayHttpStatus": 202,
            "sameRunId": False,
            "terminalEventCountAfter": 2,
            "ok": False,
            "applicable": True,
        }

    monkeypatch.setattr(runner, "_replay_finalize", failed_replay)
    receipt = runner.run_cases(
        _write_supported_manifest(tmp_path), tmp_path / "run", case_ids=["multiwall-source-unsupported"]
    )
    assert receipt["finalizeReplay"]["ok"] is False
    assert any("finalizeReplay" in blocker for blocker in receipt["blockers"])
    assert receipt["ok"] is False


def test_one_geometry_under_two_identities_fails_cross_capture(tmp_path):
    """The same source and wire under two case ids produce different identity
    stamps (distinct raw merged-scene hashes) but identical geometry — the
    cross-capture check must fail on normalized geometry, not pass on stamps."""
    receipt = runner.run_cases(_write_twin_manifest(tmp_path), tmp_path / "run")
    cross = receipt["crossCapture"]
    assert cross["status"] == "evaluated"
    assert cross["distinctScenes"] is True, "identity stamps alone differ"
    assert cross["distinctNormalizedGeometry"] is False, "the geometry is byte-identical"
    assert any("crossCapture" in blocker for blocker in receipt["blockers"])
    assert receipt["ok"] is False


def test_a_declared_negative_that_solves_fails_the_suite(tmp_path, monkeypatch):
    """If the converter someday accepts a declared negative's source, the case
    must fail loudly — a negative case that solves is never a success."""

    placement_adapter = importlib.import_module("server.placement.adapter")
    real_convert = placement_adapter.convert_scene
    valid_wire = real_convert(_supported_source_a())

    monkeypatch.setattr(placement_adapter, "convert_scene", lambda document: json.loads(json.dumps(valid_wire)))
    receipt = runner.run_cases(
        _write_supported_manifest(tmp_path), tmp_path / "run", case_ids=["multiwall-source-unsupported"]
    )
    case = receipt["cases"][0]
    assert case["status"] == "solved"
    assert any("produced a successful placement" in mismatch for mismatch in case["mismatches"])
    assert receipt["ok"] is False


def test_run_cases_restores_the_process_environment(tmp_path):
    """The dev flags must not leak into the rest of the pytest process: any later
    test asserting env-derived defaults would become order-dependent."""
    before = dict(os.environ)
    runner.run_cases(MANIFEST, tmp_path / "run", case_ids=["multiwall-source-unsupported"])
    assert dict(os.environ) == before


@pytest.mark.parametrize(
    "objects_value", [pytest.param(None, id="null-objects"), pytest.param("omit", id="missing-objects")]
)
def test_a_format_correct_objects_3d_without_an_objects_list_refuses(tmp_path, monkeypatch, objects_value):
    """Through the actual capture stage: a format-correct objects_3d artifact whose
    objects list is missing or null is a malformed manifest and must refuse
    placement with placement_objects_3d_invalid — never decode as an empty,
    hazard-free scene — and no placement artifact may exist (the refusal is not a
    fallback success)."""
    body = {"format": "hss-objects-3d/v0"}
    if objects_value != "omit":
        body["objects"] = None

    def malformed_objects_3d(ctx):
        return write_files(ctx, {"objects/objects_3d.json": json_bytes(body)})

    monkeypatch.setattr(runner, "_injected_objects_3d_run", malformed_objects_3d)
    receipt = runner.run_cases(_write_supported_manifest(tmp_path), tmp_path / "run", case_ids=[runner.TAP_CASE_ID])
    case = receipt["cases"][0]

    assert case["status"] == "refused", case
    assert case["refusal"]["code"] == "placement_objects_3d_invalid", case["refusal"]
    assert case["hasPlacementArtifact"] is False, "a refused placement must not publish an artifact"
    assert case["solverDecision"] is None
    assert receipt["summary"]["solved"] == 0
    assert receipt["ok"] is False, "an undeclared positive-case refusal is a blocker"
