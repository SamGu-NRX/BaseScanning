"""The typed export validator: one correct answer per value, tested in isolation.

Every case builds a valid artifact, changes one value, and requires the
exact category. A planted canary must be rejected wherever it lands, and
the error must not quote it.
"""

from __future__ import annotations

import copy
import json

import pytest
from conftest import FAST, canary, only_run_dir, run_cli

from hunter_local_ingest.export import (
    ExportContext,
    ExportError,
    validate_check_line,
    validate_manifest,
    validate_summary,
)
from hunter_local_ingest.modes import Status, Transport
from hunter_local_ingest.receipts import MANIFEST, SUMMARY, Check, context_for
from hunter_local_ingest.runner import FLAG_KINDS

CTX = context_for(frozenset({"synthetic/scan00.bin"}), "synthetic-000007")


def _line(observed: dict, check_id: str = "lifecycle.create") -> dict:
    return Check(check_id, Transport.FAKE_HUNTER, Status.PASS, observed).as_json()


def _rejects(line: dict, category: str) -> ExportError:
    with pytest.raises(ExportError) as exc:
        validate_check_line(line, CTX)
    assert exc.value.category == category
    return exc.value


def test_valid_line_passes():
    validate_check_line(
        _line(
            {
                "http": {"http_status": 201, "body_sha256": "ab" * 6, "fields": ["captureId"]},
                "contract": {"captureId": "id:" + "0" * 12, "status": "uploading"},
                "violations": [],
                "tampered": "synthetic/scan00.bin",
            }
        ),
        CTX,
    )


@pytest.mark.parametrize(
    "where",
    [
        lambda c: {"contract": {"status": c}},
        lambda c: {"contract": {"files": [{"path": c}]}},
        lambda c: {"batches": [{"contract": {"committed": [c]}}]},
        lambda c: {"violations": [c]},
        lambda c: {"note": c},
        lambda c: {"after_restart": [{"http": {"error": c}}]},
    ],
)
def test_canary_value_rejected_at_any_depth(where):
    marker = canary("v")
    error = _rejects(_line(where(marker)), "unsafe-string")
    assert marker not in str(error)


def test_canary_key_rejected():
    marker = canary("k")
    error = _rejects(_line({"contract": {marker: 1}}), "unexpected-key")
    assert marker not in str(error)


def test_numbers_must_be_finite_and_bounded():
    _rejects(_line({"answers": float("nan")}), "non-finite-number")
    _rejects(_line({"answers": float("inf")}), "non-finite-number")
    _rejects(_line({"answers": 2**60}), "number-out-of-range")


def test_registered_id_and_constant_text_only():
    _rejects({**_line({}), "id": "lifecycle.bogus"}, "unregistered-check-id")
    _rejects({**_line({}), "oracle": "something else"}, "oracle-not-registered-text")
    _rejects({**_line({}), "expected": canary("e")}, "expected-not-registered-text")
    _rejects({**_line({}), "transport": "REAL_MIRROR_CPU"}, "enum")
    _rejects({**_line({}), "extra": 1}, "unexpected-key")


def test_undeclared_path_is_unsafe_but_declared_path_is_fine():
    validate_check_line(_line({"tampered": "synthetic/scan00.bin"}), CTX)
    _rejects(_line({"tampered": "synthetic/scan01.bin"}), "unsafe-string")


@pytest.fixture()
def real_artifacts(fake, plan_path, tmp_path):
    out = tmp_path / "runs"
    with fake() as api:
        assert (
            run_cli(
                [
                    "--endpoint",
                    api.base_url,
                    "--plan-file",
                    str(plan_path),
                    "--out",
                    str(out),
                    "--backend-mode",
                    "FAKE_HUNTER",
                    *FAST,
                ]
            )
            == 0
        )
    run_dir = only_run_dir(out)
    manifest = json.loads((run_dir / MANIFEST).read_text())
    summary = json.loads((run_dir / SUMMARY).read_text())
    fixture = manifest["fixture"]
    ctx = context_for(frozenset(f["path"] for f in fixture["files"]), fixture["packet_id"])
    return manifest, summary, ctx


@pytest.mark.parametrize(
    ("mutate", "category"),
    [
        (lambda m, c: m["backend"].update(verified=True), "backend-mode-is-never-verified"),
        (lambda m, c: m["command"].extend(["--token", c]), "unsafe-command-token"),
        (lambda m, c: m["command"].append(c), "unsafe-command-token"),
        (lambda m, c: m["restart"]["argv"].append(c), "unsafe-command-token"),
        (lambda m, c: m["runtime"].update(release=c + " x"), "runtime-text"),
        (lambda m, c: m["fixture"]["files"][0].update(path=c), "undeclared-path"),
        (lambda m, c: m.update(endpoint=f"{c}.example:80"), "not-an-endpoint-label"),
        (lambda m, c: m.update(extra=c), "unexpected-key"),
        (lambda m, c: m["settings"].update(poll_interval_s=float("nan")), "non-finite-number"),
        (lambda m, c: m["fixture"].update(seed=None), "synthetic-fixture-requires-a-seed"),
        (
            lambda m, c: m["fixture"].update(generator="gen_miniarkit_packet"),
            "fixture-generator-does-not-match-plan",
        ),
    ],
)
def test_manifest_mutations_rejected(real_artifacts, mutate, category):
    manifest, _summary, ctx = real_artifacts
    validate_manifest(manifest, ctx, FLAG_KINDS)
    bad = copy.deepcopy(manifest)
    marker = canary("m")
    mutate(bad, marker)
    with pytest.raises(ExportError) as exc:
        validate_manifest(bad, ctx, FLAG_KINDS)
    assert exc.value.category == category
    assert marker not in str(exc.value)


def test_manifest_generator_must_match_the_plan_family(real_artifacts):
    manifest, _summary, ctx = real_artifacts
    bad = copy.deepcopy(manifest)
    bad["fixture"]["generator"] = "gen_miniarkit_packet"
    bad["fixture"]["seed"] = None
    with pytest.raises(ExportError) as exc:
        validate_manifest(bad, ctx, FLAG_KINDS)
    assert exc.value.category == "fixture-generator-does-not-match-plan"


def test_label_must_be_label_shaped(real_artifacts):
    """The label is operator-chosen public text; only its shape is enforced."""
    manifest, _summary, ctx = real_artifacts
    bad = copy.deepcopy(manifest)
    bad["label"] = "has space"
    with pytest.raises(ExportError) as exc:
        validate_manifest(bad, ctx, FLAG_KINDS)
    assert exc.value.category == "not-a-label"


@pytest.mark.parametrize(
    ("mutate", "category"),
    [
        (lambda s: s["failed"].append("not.a.check"), "unregistered-check-id"),
        (lambda s: s["mode"].update(real_mirror_cpu="ESTABLISHED"), "enum"),
        (lambda s: s["mode"].update(backend_verified=True), "backend-mode-is-never-verified"),
        (lambda s: s.update(verdict="GREEN"), "enum"),
        (lambda s: s["lead_questions"].append(canary("q")), "not-registered-text"),
        (lambda s: s["counts_by_transport"].update(REAL_MIRROR_CPU={"PASS": 1}), "enum"),
    ],
)
def test_summary_mutations_rejected(real_artifacts, mutate, category):
    _manifest, summary, ctx = real_artifacts
    validate_summary(summary, ctx)
    bad = copy.deepcopy(summary)
    mutate(bad)
    with pytest.raises(ExportError) as exc:
        validate_summary(bad, ctx)
    assert exc.value.category == category


def test_empty_context_admits_no_paths():
    with pytest.raises(ExportError):
        validate_check_line(_line({"tampered": "synthetic/scan00.bin"}), ExportContext())


# -- lead questions: current text for new runs, exact legacy text only to verify --

from hunter_local_ingest import export as _export  # noqa: E402

POLICY = (
    "--api-token-env opts into HUNTER_API_TOKEN for scoped API routes; "
    "token-protected proxy fault coverage remains BLOCKED."
)
FROZEN_SETS = ("23d5fc9", "1511668")


def test_new_summaries_state_the_settled_credential_policy(tmp_path):
    from hunter_local_ingest import runner
    from hunter_local_ingest.receipts import write_summary

    assert runner.LEAD_QUESTIONS is _export.LEAD_QUESTIONS
    assert POLICY in _export.LEAD_QUESTIONS
    assert not set(_export.LEGACY_LEAD_QUESTIONS) & set(_export.LEAD_QUESTIONS)
    assert len(_export.LEAD_QUESTIONS) == 4  # amendment, policy, clock, homeowner text
    write_summary(
        tmp_path,
        "0123456789abcdef",
        [],
        declared_backend="FAKE_HUNTER",
        lead_questions=runner.LEAD_QUESTIONS,
        ctx=ExportContext(),
    )
    written = json.loads((tmp_path / "summary.json").read_text())["lead_questions"]
    assert written == list(_export.LEAD_QUESTIONS)
    assert "no CLI flag supplies one yet" not in json.dumps(written)


def test_legacy_question_validates_only_verbatim(real_artifacts):
    _manifest, summary, ctx = real_artifacts
    legacy = _export.LEGACY_LEAD_QUESTIONS[0]
    old = copy.deepcopy(summary)
    old["lead_questions"][1] = legacy
    validate_summary(old, ctx)  # a receipt written before the policy change
    for text in (legacy.replace("yet.", "yet!"), legacy + " ", canary("q"), POLICY.upper()):
        bad = copy.deepcopy(summary)
        bad["lead_questions"][1] = text
        with pytest.raises(ExportError) as exc:
            validate_summary(bad, ctx)
        assert exc.value.category == "not-registered-text"


@pytest.mark.parametrize("frozen", FROZEN_SETS)
def test_frozen_receipt_sets_verify_with_the_current_verifier(frozen, tmp_path):
    import hashlib
    import shutil
    from pathlib import Path

    from hunter_local_ingest.verify import verify_run

    root = Path(__file__).resolve().parents[1] / "evidence" / frozen
    run_dirs = sorted(p.parent for p in root.glob("*/*/summary.json"))
    assert run_dirs, root
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    legacy_seen = False
    for run_dir in run_dirs:
        copy_dir = tmp_path / run_dir.parent.name / run_dir.name
        shutil.copytree(run_dir, copy_dir)
        for path in copy_dir.iterdir():
            path.chmod(0o444)  # git checks files out writable; re-seal the copy only
        assert verify_run(copy_dir) == [], run_dir
        summary = json.loads((run_dir / "summary.json").read_text())
        legacy_seen |= bool(set(summary["lead_questions"]) & set(_export.LEGACY_LEAD_QUESTIONS))
    assert legacy_seen, "these frozen sets were written with the legacy text"
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    assert after == before, "the frozen files are never rewritten"
