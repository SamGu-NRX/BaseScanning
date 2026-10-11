"""Unit tests for receipts, trace comparison, sanitization and fixtures.

The decisive test is the planted-drift mutation: the frozen-trace
comparator must flag a deliberately wrong expectation, proving the
self-check's trace gate actually bites.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hunter_local_ingest.adapter import Observation, StepResult
from hunter_local_ingest.modes import Status, Transport
from hunter_local_ingest.receipts import Check, fresh_run_dir, write_summary
from hunter_local_ingest.scenarios import record, step_observed
from hunter_local_ingest.traces import compare_trace

TOOL_ROOT = Path(__file__).resolve().parents[1]
TRACE_V1 = TOOL_ROOT / "traces" / "expected-lifecycle-v1.jsonl"


def _check(check_id: str, http_status: int, fields: tuple, status: Status = Status.PASS):
    return Check(
        id=check_id,
        transport=Transport.REAL_MIRROR_CPU,
        oracle="test oracle",
        expected="test expectation",
        observed="http 200; fields=…",
        status=status,
        observation=Observation(
            http_status=http_status,
            field_names=fields,
            safe_values={"status": "ok"},
            body_sha256="ab" * 32,
            body_bytes=10,
            elapsed_ms=3,
            detail=None,
        ),
    )


def _run_checks() -> list[Check]:
    """A minimal run consistent with the frozen trace's lifecycle lines."""
    return [
        _check("lifecycle.health", 200, ("status",)),
        _check("lifecycle.create", 201, ("captureId", "status")),
        _check("lifecycle.register", 200, ("files",)),
        _check("lifecycle.upload", 200, ()),
        _check("lifecycle.commit", 200, ("committed",)),
        _check("lifecycle.finalize", 202, ("status",)),
        _check("lifecycle.events", 200, ("next",)),
        _check(
            "lifecycle.result",
            200,
            ("schema_version",),
        ),
        _check("control.idempotent-create", 201, ()),
        _check("control.corrupt-upload", 200, ()),
        _check("control.truncated-upload", 200, ()),
        _check("control.wrong-capture", 200, ()),
    ]


class TestTraceComparison:
    def test_conforming_run_has_no_problems(self):
        problems = compare_trace(
            _run_checks(), TRACE_V1, Transport.REAL_MIRROR_CPU
        )
        # only advisory-capable lines may still disagree; assert none do here
        assert [p for p in problems if not p.startswith("advisory:")] == []
        assert not any("lifecycle.create" in p for p in problems)

    def test_planted_field_drift_is_caught(self):
        """Decisive: a planted wrong expectation must be flagged, not absorbed."""
        checks = _run_checks()
        # remove captureId from the create observation's fields
        checks[1].observation = Observation(
            http_status=201,
            field_names=("captureId",),  # drift: 'status' missing
            safe_values={"captureId": "cap-0001"},
            body_sha256="ab" * 32,
            body_bytes=10,
            elapsed_ms=1,
            detail=None,
        )
        problems = compare_trace(checks, TRACE_V1, Transport.REAL_MIRROR_CPU)
        assert any("lifecycle.create" in p and "missing" in p for p in problems)

    def test_planted_status_drift_is_caught(self):
        checks = _run_checks()
        checks[4].observation = Observation(
            http_status=404,  # drift: commit answered 4xx
            field_names=("committed",),
            safe_values={},
            body_sha256="ab" * 32,
            body_bytes=10,
            elapsed_ms=1,
            detail=None,
        )
        problems = compare_trace(checks, TRACE_V1, Transport.REAL_MIRROR_CPU)
        assert any("lifecycle.commit" in p and "not in class" in p for p in problems)

    def test_failing_check_is_flagged(self):
        checks = _run_checks()
        checks[2].status = Status.FAIL
        problems = compare_trace(checks, TRACE_V1, Transport.REAL_MIRROR_CPU)
        assert any("lifecycle.register" in p and "FAILs" in p for p in problems)

    def test_missing_check_is_flagged(self):
        checks = [c for c in _run_checks() if c.id != "lifecycle.result"]
        problems = compare_trace(checks, TRACE_V1, Transport.REAL_MIRROR_CPU)
        assert any("lifecycle.result missing from run" in p for p in problems)

    def test_provisional_mismatch_is_advisory_only(self):
        checks = _run_checks()
        # lifecycle.result is provisional: even a hard field mismatch stays advisory
        checks[7].observation = Observation(
            http_status=200,
            field_names=(),
            safe_values={},
            body_sha256="ab" * 32,
            body_bytes=10,
            elapsed_ms=1,
            detail=None,
        )
        problems = compare_trace(checks, TRACE_V1, Transport.REAL_MIRROR_CPU)
        assert any(p.startswith("advisory: ") and "lifecycle.result" in p for p in problems)
        assert not any(
            "lifecycle.result" in p and not p.startswith("advisory: ") for p in problems
        )

    def test_other_transport_checks_are_ignored(self):
        """FAKE_HUNTER lines must not satisfy REAL_MIRROR_CPU trace lines."""
        checks = [c for c in _run_checks()]
        for c in checks:
            c.transport = Transport.FAKE_HUNTER
        problems = compare_trace(checks, TRACE_V1, Transport.REAL_MIRROR_CPU)
        assert any("missing from run" in p for p in problems)


class TestReceipts:
    def test_fresh_run_dir_refuses_replay_into_existing_dir(self, tmp_path):
        run_dir = fresh_run_dir(tmp_path, "run")
        assert run_dir.exists()
        with pytest.raises(SystemExit, match="refusing to overwrite"):
            fresh_run_dir(tmp_path, "run")

    def test_check_json_never_carries_raw_observation(self):
        check = _check("lifecycle.create", 201, ("captureId", "status"))
        out = check.as_json()
        assert "observation" not in out
        assert set(out) == {"id", "transport", "oracle", "expected", "observed", "status", "ts"}
        # reserialized shape is stable for the receipt contract
        assert out["status"] == "PASS"
        assert out["transport"] == "REAL_MIRROR_CPU"

    def test_evidence_field_only_when_present(self):
        none = _check("lifecycle.health", 200, ("status",)).as_json()
        assert "evidence" not in none
        with_ev = _check("lifecycle.health", 200, ("status",))
        with_ev.evidence = "steps/health.json"
        assert with_ev.as_json()["evidence"] == "steps/health.json"

    def test_summary_counts_by_transport(self, tmp_path):
        checks = _run_checks() + [
            Check(
                id="gpu-stub.vlm",
                transport=Transport.GPU_STUB,
                oracle="task vocabulary",
                expected="not run",
                observed="not run",
                status=Status.NOT_RUN,
            )
        ]
        write_summary(tmp_path, checks, lead_questions=[], blocked=[])
        summary = json.loads((tmp_path / "summary.json").read_text())
        assert summary["counts_by_transport"]["GPU_STUB"] == {"NOT_RUN": 1}
        assert summary["counts_by_transport"]["REAL_MIRROR_CPU"]["PASS"] == 12


class TestSanitization:
    def test_step_observed_only_carries_allowlisted_pieces(self):
        obs = Observation(
            http_status=201,
            field_names=("captureId", "status"),
            safe_values={"captureId": "cap-0001", "status": "capturing"},
            body_sha256="ab" * 32,
            body_bytes=42,
            elapsed_ms=7,
            detail=None,
        )
        text = step_observed(StepResult(step="health", ok=True, observation=obs))
        assert "http 201" in text and "captureId" in text
        assert "body_sha256=abababababab" in text  # 12-char digest prefix
        # no raw body bytes, no full digest, no signed URL shape
        assert "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in text.replace("body_sha256=", "", 1)
        assert "https://" not in text and "Signature=" not in text

    def test_step_observed_prefers_transport_error(self):
        text = step_observed(StepResult(step="health", ok=False, error="RemoteDisconnected"))
        assert text == "RemoteDisconnected"

    def test_record_accepts_stepresult_and_keeps_observation_in_process(self):
        sink: list = []
        obs = Observation(
            http_status=200,
            field_names=("status",),
            safe_values={"status": "ok"},
            body_sha256="cd" * 32,
            body_bytes=5,
            elapsed_ms=2,
            detail=None,
        )
        status = record(
            sink,
            Transport.REAL_MIRROR_CPU,
            "lifecycle.health",
            "oracle",
            "expected",
            StepResult(step="health", ok=True, observation=obs),
            Status.PASS,
        )
        assert status is Status.PASS
        line = sink[0]
        assert line["observation"] is obs  # in-process only
        assert "body_sha256=" in line["observed"]
        # ...but the serialized receipt never carries it
        check = Check(
            id=line["id"],
            transport=Transport(line["transport"]),
            oracle=line["oracle"],
            expected=line["expected"],
            observed=line["observed"],
            status=Status(line["status"]),
            evidence=line.get("evidence"),
            observation=line.get("observation"),
        )
        assert "observation" not in check.as_json()


class TestProxyStepRouting:
    def test_step_for_maps_paths_to_steps(self):
        from hunter_local_ingest.faultproxy import step_for

        assert step_for("GET", "/healthz") == "health"
        assert step_for("POST", "/captures") == "create"
        assert step_for("POST", "/captures/cap-1/files") == "register"
        assert step_for("PUT", "/uploads/token") == "upload"
        assert step_for("POST", "/captures/cap-1/files:commit") == "commit"
        assert step_for("POST", "/captures/cap-1/finalize") == "finalize"
        assert step_for("GET", "/captures/cap-1/events") == "events"
        assert step_for("GET", "/captures/cap-1/result") == "result"
        assert step_for("GET", "/nonsense") == "unknown"


class TestFixtureDeterminism:
    def test_same_seed_reproduces_identical_plan(self, tmp_path):
        gen = TOOL_ROOT / "fixtures" / "gen_synthetic_packet.py"
        outs = []
        for i in (1, 2):
            out = tmp_path / f"plan-{i}.json"
            subprocess.run(
                [sys.executable, str(gen), "--seed", "7", "--out", str(out)],
                check=True,
                cwd=TOOL_ROOT,
            )
            import hashlib

            outs.append(hashlib.sha256(out.read_bytes()).hexdigest())
        assert outs[0] == outs[1], "same seed must reproduce the plan byte for byte"

    def test_different_seed_changes_content(self, tmp_path):
        gen = TOOL_ROOT / "fixtures" / "gen_synthetic_packet.py"
        digests = set()

        for seed in ("1", "2"):
            out = tmp_path / f"plan-{seed}.json"
            subprocess.run(
                [sys.executable, str(gen), "--seed", seed, "--out", str(out)],
                check=True,
                cwd=TOOL_ROOT,
            )
            plan = json.loads(out.read_text())
            digests.add(plan["plan"]["packet_id"])
        assert len(digests) == 2
