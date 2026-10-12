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
