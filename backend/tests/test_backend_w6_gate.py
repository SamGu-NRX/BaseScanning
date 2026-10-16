"""The wave-6 acceptance gate: staged receipts in, classifications out.

Wave 6 (the placement stage behind Hunter's capture front door, PRs #268/#269)
left three kinds of machine-readable evidence. This gate consumes the staged
copies under ``tests/fixtures/w6/`` and classifies every row, asserting the
summary counts and the required blocked reasons from the receipts' JSON — never
from console or SUMMARY prose:

- ``suite.json`` — the eval-suite receipt (``hss-eval-suite/v0``, the contract
  PR #263 pinned). Its producer (``make evals``, PR #261) is not merged into
  this base branch, so the staged copy was produced by running that producer
  unmodified at its own head (``9b177de6``, PR #263's branch) in a throwaway
  worktree; ``backend/docs/STATUS-W6.md`` records the command. No threshold or
  evaluator code changed.
- ``gates.json`` — the offline gates receipt (``hss-gates/v0``) from
  ``make gates-offline`` on this branch; it carries the expired SD-96
  known-failure gate.
- ``frontdoor-receipt.json`` + ``placement-result.json`` — one capture-front-door
  journey (PR #269's own helpers, SD-73 dev settings) and the placement stage's
  decision artifact from that journey.

Classification mirrors the receipt contract PR #263 pinned: a FAIL whose note
names the unavailable asset is BLOCKED (fail-closed on gitignored fixtures, with
the produce hint); every other FAIL stays FAIL — a real threshold miss or the
evaluator's fail-closed cascade, kept visible rather than re-labeled.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
W6 = BACKEND / "tests" / "fixtures" / "w6"
SCENE_FIXTURE = REPO_ROOT / "server" / "tests" / "fixtures" / "example-scene.json"
RESULT_SCHEMA = json.loads((REPO_ROOT / "server" / "schemas" / "result.schema.json").read_text())

SUITE = json.loads((W6 / "suite.json").read_text())
GATES = json.loads((W6 / "gates.json").read_text())
FRONTDOOR = json.loads((W6 / "frontdoor-receipt.json").read_text())
PLACEMENT = json.loads((W6 / "placement-result.json").read_text())

# The receipt contract's blocked-fixture hints: the note names the missing asset
# and how to produce it. Anything else that FAILs keeps the FAIL label.
BLOCKED_MARKERS = ("occluders_missing", "recon asset missing")


def _classify(row: dict) -> str:
    if row["status"] == "PASS":
        return "PASS"
    if row["status"] == "MEASURED":
        return "MEASURED"
    if row["status"] == "FAIL" and any(marker in row.get("note", "") for marker in BLOCKED_MARKERS):
        return "BLOCKED"
    if row["status"] == "FAIL":
        return "FAIL"
    return "WARN"


def _classified() -> dict[str, list[dict]]:
    classes: dict[str, list[dict]] = {"PASS": [], "FAIL": [], "MEASURED": [], "BLOCKED": [], "WARN": []}
    for row in SUITE["rows"]:
        classes[_classify(row)].append(row)
    return classes


def _tally(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


def _fixture_sha() -> str:
    return hashlib.sha256(SCENE_FIXTURE.read_bytes()).hexdigest()


# --- the eval-suite receipt (PRs #261/#263) ----------------------------------


def test_suite_receipt_summary_counts() -> None:
    assert SUITE["format"] == "hss-eval-suite/v0"
    rows = SUITE["rows"]
    assert len(rows) == 27
    # The receipt's own summary reconstructs exactly from the row statuses.
    assert SUITE["summary"] == {"pass": 3, "fail": 21, "warn": 0, "measured": 3}
    tally = _tally(rows)
    assert tally["PASS"] == SUITE["summary"]["pass"] == 3
    assert tally["FAIL"] == SUITE["summary"]["fail"] == 21
    assert tally.get("WARN", 0) == SUITE["summary"]["warn"] == 0
    assert tally["MEASURED"] == SUITE["summary"]["measured"] == 3
    assert set(tally) <= {"PASS", "FAIL", "WARN", "MEASURED"}, "statuses stay in the closed vocabulary"

    # The W6 classification over the same rows: blocked fixtures, not hidden failures.
    classes = _classified()
    assert {key: len(values) for key, values in classes.items()} == {
        "PASS": 3,
        "FAIL": 16,
        "MEASURED": 3,
        "BLOCKED": 5,
        "WARN": 0,
    }


def test_suite_blocked_rows_name_the_missing_assets_with_evidence_paths() -> None:
    classes = _classified()

    # The evaluator's occluders_missing failures: the laser/occluder assets are
    # absent, and the note carries the produce hint.
    occluder_rows = [row for row in classes["BLOCKED"] if "occluders_missing" in row["note"]]
    assert len(occluder_rows) == 3
    assert {row["id"] for row in occluder_rows} == {
        "pipeline/eth3d-facade",
        "pipeline/eth3d-electro",
        "pipeline/eth3d-delivery",
    }
    for row in occluder_rows:
        assert "laser PLY absent; run make gt" in row["note"]
        assert row["evidence_path"], row["id"]

    # The evaluator's missing-recon-PLY failures: the geometry rows fail closed
    # because the gitignored Modal-pipeline outputs are not on this machine.
    recon_rows = [row for row in classes["BLOCKED"] if "recon asset missing" in row["note"]]
    assert {row["id"] for row in recon_rows} == {
        "geometry/eth3d-facade/colmap-nonlidar",
        "geometry/eth3d-facade/colmap-oracle",
    }
    for row in recon_rows:
        assert "not a readable file" in row["note"]
        assert "scripts/modal_scene.py" in row["note"], "the produce hint survives in the receipt"
        # No produced artifact exists to point at, so the receipt's evidence_path
        # is honestly null; the expected-asset location is the row's evidence,
        # carried in the note.
        assert row["evidence_path"] is None, row["id"]


def test_suite_fail_cascade_stays_visible() -> None:
    classes = _classified()
    # The remaining FAILs are the fail-closed cascade: with the pipeline unable
    # to run, criteria and verdict rows have nothing to score. They keep the
    # FAIL label and a non-empty note — never re-labeled, never summarized away.
    assert len(classes["FAIL"]) == 16
    for row in classes["FAIL"]:
        assert row["note"], row["id"]
    assert all("produced no verdict" in row["note"] for row in classes["FAIL"] if row["group"] == "verdicts")
    cascade = [row for row in classes["FAIL"] if row["group"] == "criteria"]
    assert {row["id"] for row in cascade} == {
        "criteria/eth3d-facade/report",
        "criteria/eth3d-electro/report",
        "criteria/eth3d-delivery/report",
        "criteria/eth3d-facade/verdict_decisions",
    }


def test_suite_measured_rows_are_recorded_without_a_bar() -> None:
    classes = _classified()
    assert len(classes["MEASURED"]) == 3
    for row in classes["MEASURED"]:
        assert row["value"] is None
        assert row["note"] == "no committed labels for this profile"
        assert row["id"].endswith("/public")


def test_suite_pass_rows_carry_evidence_paths() -> None:
    for row in SUITE["rows"]:
        if row["status"] == "PASS":
            assert row["evidence_path"], row["id"]
            assert Path(BACKEND / row["evidence_path"]).exists(), row["id"]


# --- the offline gates receipt (the expired SD-96 gate) -----------------------


def test_gates_receipt_pins_the_expired_sd96_gate() -> None:
    assert GATES["format"] == "hss-gates/v0"
    rows = GATES["rows"]
    assert len(rows) == 164
    assert GATES["summary"] == {"pass": 134, "fail": 1, "known_fail": 0, "warn": 1, "skip": 28}
    tally = _tally(rows)
    assert tally == {"PASS": 134, "FAIL": 1, "WARN": 1, "SKIP": 28}

    fails = [row for row in rows if row["status"] == "FAIL"]
    assert len(fails) == 1
    row = fails[0]
    assert row["id"] == "positive/eth3d-facade/gt-laser-gravel/C/footprint3in"
    assert row["note"] == "route 9.1834 ft exceeds 9.0"
    assert row["evidence_path"] == "data/fixtures/positive/eth3d-facade/gt-laser-gravel/C/footprint3in.json"
    # The known-failure window (reason "conservative miss from near-wall
    # self-occlusion, SD-96", owner WS-09) expired 2026-10-04, so the gate
    # reports FAIL again; extending it is a WS-09 policy call, recorded in
    # backend/docs/STATUS-W6.md rather than patched.


# --- the capture-front-door receipt and the placement-stage result ------------


def test_frontdoor_receipt_is_the_terminal_manual_review_journey() -> None:
    assert FRONTDOOR["format"] == "hss-w6-frontdoor/v0"
    journey = FRONTDOOR["journey"]

    marks = [tuple(mark) for mark in journey["stage_marks"]]
    assert ("scene", "done") in marks and ("placement", "done") in marks and ("criteria", "done") in marks
    order = [marks.index(mark) for mark in (("scene", "done"), ("placement", "done"), ("criteria", "done"))]
    assert order == sorted(order), "the placement stage runs between scene and criteria"

    assert journey["terminal_events"] == ["verdict_ready"], "exactly one terminal event"
    assert journey["capture_status"] == "manual_review"
    assert journey["result_status"] == 200
    assert journey["result_outcome_kind"] == "manual_review"
    assert journey["run_status"] == "done"
    assert journey["run_outcome_kind"] == "manual_review"
    assert journey["placement_stage_status"] == "done"
    assert journey["placement_stage_error_code"] is None


def test_frontdoor_receipt_artifacts_and_direct_contract_match_the_fixture() -> None:
    artifacts = FRONTDOOR["artifacts"]
    assert artifacts["placement/placement.json"]
    assert artifacts["placement/scene.json"]
    assert artifacts["placement/scene.json_equals_committed_fixture"] is True
    assert artifacts["placement/scene.json_sha256"] == _fixture_sha(), (
        "the stage solved exactly the committed scene bytes"
    )

    direct = FRONTDOOR["direct_placements"]
    assert direct["status"] == 200
    assert direct["decision"] == "manual_review"
    assert direct["input_sha256"] == direct["committed_scene_sha256"] == _fixture_sha(), (
        "the direct POST /v1/placements contract still echoes the submitted bytes' sha256"
    )


def test_staged_placement_result_is_schema_valid_and_pinned_to_the_fixture() -> None:
    Draft202012Validator(RESULT_SCHEMA).validate(PLACEMENT)
    assert PLACEMENT["decision"] == "manual_review"
    assert PLACEMENT["stats"]["input_sha256"] == _fixture_sha()
    assert PLACEMENT["stats"]["elapsed_ms"] > 0


def test_a_fresh_front_door_journey_matches_the_staged_receipt(monkeypatch, tmp_path) -> None:
    """One fresh journey (the same helpers PR #269's tests run) must reproduce
    the staged decision bytes — everything but the solver's wall-clock timing."""

    spec = importlib.util.spec_from_file_location(
        "w6_frontdoor_reference", BACKEND / "tests" / "test_capture_placement_frontdoor.py"
    )
    assert spec and spec.loader
    fd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fd)

    from server.api.app import create_app
    from server.common.config import Settings

    for key, value in fd.TEMPLATE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("VOLUME_ROOT", str(tmp_path / "vol"))
    monkeypatch.chdir(tmp_path)

    root = tmp_path / "packet"
    shutil.copytree(fd.PACKET_FIXTURE, root)
    packet = json.loads((root / "packet.json").read_text())
    settings = Settings(
        SMP_ENVIRONMENT="dev",
        HSS_AUTH_MODE="static",
        CAPTURE_API_TOKENS=f"device:{'d' * 40}",
        UPLOAD_URL_HMAC_SECRET="h" * 32,
        STORAGE_BACKEND="volume",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "vol"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        HSS_INGEST_HOOKS="auto",
        VISION_EXTERNAL_CALLS="off",
        GIT_SHA="0123456789abcdef",
        SMP_SERVICE_NAME="capture-api",
    )
    app = create_app(settings=settings)
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {'d' * 40}"

    _, run_id, _ = fd._walk_front_door(root, packet, client, app, packet["packetId"])
    artifacts = fd._run_artifacts(app.state.deps, run_id)
    fresh = json.loads(app.state.deps.storage.get_bytes(artifacts["placement/placement.json"]))

    def projection(payload: dict) -> bytes:
        decision = json.loads(json.dumps(payload))
        decision["stats"].pop("elapsed_ms")
        return json.dumps(decision, sort_keys=True).encode()

    assert projection(fresh) == projection(PLACEMENT), (
        "a fresh journey's decision differs from the staged placement result"
    )
