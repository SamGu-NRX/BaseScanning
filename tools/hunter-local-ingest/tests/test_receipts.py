"""Receipts, integrity, traces, fixtures, the plan loader and runner exit codes."""

from __future__ import annotations

import hashlib
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import FAST, only_run_dir, read_checks, run_cli

from hunter_local_ingest import receipts
from hunter_local_ingest.fixturegen import build, write_plan
from hunter_local_ingest.modes import Status, Transport
from hunter_local_ingest.observations import Observation
from hunter_local_ingest.plan import PlanError, canonical_digest, load_plan
from hunter_local_ingest.receipts import Check, fresh_run_dir, verify_run_integrity
from hunter_local_ingest.traces import compare_trace, load_trace

# -- run directories ----------------------------------------------------------


def test_run_dirs_are_unique_within_one_second(tmp_path):
    first, id1 = fresh_run_dir(tmp_path, "run")
    second, id2 = fresh_run_dir(tmp_path, "run")
    assert first != second and id1 != id2


def test_run_dir_is_never_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts.secrets, "token_hex", lambda n: "0" * 16)
    fresh_run_dir(tmp_path, "run")
    with pytest.raises(FileExistsError):
        fresh_run_dir(tmp_path, "run")


def test_checks_file_is_created_exclusively(tmp_path):
    (tmp_path / "checks.jsonl").write_text("earlier\n")
    with pytest.raises(FileExistsError):
        receipts.ChecksWriter(tmp_path, receipts.ExportContext())
    assert (tmp_path / "checks.jsonl").read_text() == "earlier\n"


@pytest.fixture()
def finished_run(fake, plan_path, tmp_path) -> Path:
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
    assert verify_run_integrity(run_dir) == []
    return run_dir


def test_tamper_after_seal_is_detected(finished_run):
    checks = finished_run / "checks.jsonl"
    checks.chmod(0o644)
    checks.write_text(checks.read_text() + "{}\n")
    problems = verify_run_integrity(finished_run)
    assert "checks.jsonl: modified after chain entry" in problems
    assert "checks.jsonl: differs from the digest in the summary" in problems
    assert "checks.jsonl: not sealed (writable)" in problems


def test_extra_file_and_broken_chain_are_detected(finished_run):
    (finished_run / "notes.txt").write_text("x")
    chain = finished_run / "privacy-chain.jsonl"
    lines = chain.read_text().splitlines()
    first = json.loads(lines[0])
    first["sha256"] = "0" * 64
    chain.chmod(0o644)
    chain.write_text("\n".join([json.dumps(first, sort_keys=True), *lines[1:]]) + "\n")
    chain.chmod(0o444)
    problems = verify_run_integrity(finished_run)
    assert "run-dir: 1 unexpected file(s)" in problems
    assert any("chain break" in p for p in problems)


def test_receipt_digests_match_the_files(finished_run):
    summary = json.loads((finished_run / "summary.json").read_text())
    for name, digest in summary["artifacts"].items():
        assert hashlib.sha256((finished_run / name).read_bytes()).hexdigest() == digest


# -- frozen traces ---------------------------------------------------------------


def _check(check_id, status=Status.PASS, http=200, fields=()):
    obs = Observation(http_status=http, field_names=tuple(fields))
    return Check(check_id, Transport.REAL_API, status, {}, obs)


def _conforming():
    fields = {line["id"]: line["field_names"] or () for line in load_trace()}
    http = {"control.wrong-capture": 404}
    return [_check(i, http=http.get(i, 200), fields=f) for i, f in fields.items()]


def test_trace_is_package_data_and_v1_is_kept():
    assert len(load_trace()) == 12
    assert load_trace("expected-lifecycle-v1.jsonl")[0]["id"] == "lifecycle.health"
    # v2 ships unedited; v3 differs from it only by the upload and commit branch
    v2, v3 = load_trace("expected-lifecycle-v2.jsonl"), load_trace()
    assert [{k: v for k, v in line.items() if k != "no_request_branch"} for line in v3] == v2
    assert [line["id"] for line in v3 if "no_request_branch" in line] == [
        "lifecycle.upload",
        "lifecycle.commit",
    ]


def test_conforming_run_agrees_with_the_trace():
    result = compare_trace(_conforming(), load_trace(), Transport.REAL_API)
    assert result.status is Status.PASS and result.compared == 12


@pytest.mark.parametrize(
    ("mutate", "kind"),
    [
        (lambda cs: cs.pop(1), "missing-from-run"),
        (lambda cs: setattr(cs[2], "status", Status.FAIL), "status-fail"),
        (
            lambda cs: setattr(
                cs[4],
                "observation",
                Observation(http_status=404, field_names=("committed", "mismatch", "notFound")),
            ),
            "http-class",
        ),
        (
            lambda cs: setattr(
                cs[1], "observation", Observation(http_status=201, field_names=("captureId",))
            ),
            "fields-missing",
        ),
        (lambda cs: setattr(cs[0], "observation", None), "no-observation"),
    ],
)
def test_trace_disagreements_are_caught(mutate, kind):
    checks = _conforming()
    mutate(checks)
    result = compare_trace(checks, load_trace(), Transport.REAL_API)
    assert result.status is Status.FAIL
    assert kind in {k for _i, k in result.disagreements}


@pytest.mark.parametrize("status", [Status.BLOCKED, Status.NOT_RUN])
def test_blocked_or_not_run_never_reads_as_agreement(status):
    checks = _conforming()
    checks[5].status = status
    result = compare_trace(checks, load_trace(), Transport.REAL_API)
    assert result.status is Status.BLOCKED
    assert result.not_comparable == (("lifecycle.finalize", status.value),)


def test_fake_rows_never_satisfy_a_real_trace():
    checks = _conforming()
    for check in checks:
        check.transport = Transport.FAKE_HUNTER
    result = compare_trace(checks, load_trace(), Transport.REAL_API)
    assert result.status is Status.FAIL and result.compared == 0


# -- fixtures and the plan loader ------------------------------------------------


def test_same_seed_same_bytes_and_different_seed_different_file_bytes(tmp_path):
    a1, a2 = tmp_path / "a1.json", tmp_path / "a2.json"
    write_plan(a1, seed=1)
    write_plan(a2, seed=1)
    assert a1.read_bytes() == a2.read_bytes()
    one, two = build(1, 3, 4), build(2, 3, 4)
    files1 = {f["path"]: f for f in one["plan"]["files"]}
    files2 = {f["path"]: f for f in two["plan"]["files"]}
    assert files1.keys() == files2.keys()
    for path in files1:
        assert files1[path]["bytes"] == files2[path]["bytes"] or path.startswith("streams/")
        assert files1[path]["content_hex"] != files2[path]["content_hex"], path
    assert one["plan"]["packet_sha256"] != two["plan"]["packet_sha256"]


def test_fixture_digest_binds_the_emitted_bytes(tmp_path, plan_path):
    loaded = load_plan(plan_path)
    assert loaded.plan_file_sha256 == hashlib.sha256(plan_path.read_bytes()).hexdigest()
    document = json.loads(plan_path.read_text())
    assert canonical_digest(document) == loaded.plan_sha256
    for f in loaded.plan.files:
        assert f.sha256 == hashlib.sha256(f.content).hexdigest()
    assert loaded.packet_sha256 == hashlib.sha256(loaded.plan.packet).hexdigest()


def _flip(content_hex: str) -> str:
    data = bytes.fromhex(content_hex)
    return (bytes([data[0] ^ 0xFF]) + data[1:]).hex()


def _rewrite(path: Path, mutate) -> None:
    document = json.loads(path.read_text())
    mutate(document)
    document["generator"]["sha256_of_plan"] = canonical_digest(document)
    path.write_text(json.dumps(document))


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (
            lambda d: d["plan"]["files"][0].update(
                content_hex=_flip(d["plan"]["files"][0]["content_hex"])
            ),
            "plan.files[0].sha256",
        ),
        (
            lambda d: d["plan"]["files"][0].update(path="CANARY-path-0123/x.bin"),
            "plan.files[0].path",
        ),
        (lambda d: d["plan"].update(packet_id="synthetic-000008"), "plan.packet_id"),
        (lambda d: d["plan"]["create_request"].update(flow="free"), "plan.create_request.flow"),
        (lambda d: d["plan"]["create_request"].update(extra=1), "plan.create_request"),
        (lambda d: d["plan"].update(packet_sha256="0" * 64), "plan.packet_sha256"),
        (lambda d: d["plan"]["files"].pop(), "packet.files"),
    ],
)
def test_plan_loader_refuses(plan_path, mutate, field):
    _rewrite(plan_path, mutate)
    with pytest.raises(PlanError) as exc:
        load_plan(plan_path)
    assert exc.value.field == field


def test_plan_digest_must_match(plan_path):
    document = json.loads(plan_path.read_text())
    document["plan"]["files"][0]["role"] = "keyframe"
    plan_path.write_text(json.dumps(document))
    with pytest.raises(PlanError) as exc:
        load_plan(plan_path)
    assert exc.value.field == "generator.sha256_of_plan"


def test_seed_must_match_the_plan(plan_path):
    with pytest.raises(PlanError) as exc:
        load_plan(plan_path, expected_seed=8)
    assert exc.value.problem == "differs from --seed"


# -- runner exit codes -------------------------------------------------------------


class _Always500(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def _reply(self) -> None:
        body = b'{"errors":[{"code":"harness_forced_500"}]}'
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = _reply


def test_exit_one_on_fail(plan_path, tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Always500)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        out = tmp_path / "runs"
        code = run_cli(
            [
                "--endpoint",
                f"http://127.0.0.1:{server.server_address[1]}/v1",
                "--plan-file",
                str(plan_path),
                "--out",
                str(out),
                "--backend-mode",
                "REAL_API",
                "--max-retries",
                "0",
                *FAST,
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
    assert code == 1
    rows = read_checks(only_run_dir(out))
    assert rows["lifecycle.health"]["status"] == "FAIL"
    assert rows["lifecycle.create"]["status"] == "FAIL"
    assert rows["lifecycle.register"]["status"] == "BLOCKED"


def test_exit_four_when_blocked(plan_path, tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    out = tmp_path / "runs"
    code = run_cli(
        [
            "--endpoint",
            f"http://127.0.0.1:{port}/v1",
            "--plan-file",
            str(plan_path),
            "--out",
            str(out),
            "--backend-mode",
            "REAL_API",
            *FAST,
        ]
    )
    assert code == 4
    run_dir = only_run_dir(out)
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["verdict"] == "BLOCKED" and "lifecycle.create" in summary["blocked"]
    rows = read_checks(run_dir)
    assert rows["trace.expected-lifecycle-v3"]["status"] == "BLOCKED"
    assert rows["trace.expected-lifecycle-v3"]["observed"]["compared"] == 0
    assert verify_run_integrity(run_dir) == []


def test_exit_three_on_a_refused_plan(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"plan": {}}))
    assert (
        run_cli(
            [
                "--endpoint",
                "http://127.0.0.1:9/v1",
                "--plan-file",
                str(bad),
                "--backend-mode",
                "FAKE_HUNTER",
                "--out",
                str(tmp_path / "o"),
            ]
        )
        == 3
    )
    assert "plan refused: plan-file: unexpected or missing keys" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_exit_two_on_usage_errors(plan_path, capsys):
    with pytest.raises(SystemExit) as exc:
        run_cli(["--plan-file", str(plan_path), "--backend-mode", "FAKE_HUNTER"])
    assert exc.value.code == 2
    assert "usage:missing:--endpoint" in capsys.readouterr().err
    assert (
        run_cli(
            [
                "--endpoint",
                "http://api.example/v1",
                "--plan-file",
                str(plan_path),
                "--backend-mode",
                "FAKE_HUNTER",
            ]
        )
        == 2
    )
    assert "endpoint:base_insecure" in capsys.readouterr().err


def test_exit_five_when_an_unsafe_value_reaches_the_receipt_boundary(
    fake, plan_path, tmp_path, monkeypatch, capsys
):
    """Mutation control: break the decoder's redaction and the validator must stop it."""
    from hunter_local_ingest import contract

    monkeypatch.setattr(contract, "pub_id", lambda value: str(value))
    out = tmp_path / "runs"
    with fake() as api:
        code = run_cli(
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
    assert code == 5
    err = capsys.readouterr().err
    assert "receipts: FAILED" in err and "unsafe-string" in err
    run_dir = only_run_dir(out)
    assert "cap_fake" not in (run_dir / "checks.jsonl").read_text()


def test_real_api_label_is_a_declaration(fake, plan_path, tmp_path):
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
                    "REAL_API",
                    *FAST,
                ]
            )
            == 0
        )
    run_dir = only_run_dir(out)
    manifest = json.loads((run_dir / "run-manifest.json").read_text())
    summary = json.loads((run_dir / "summary.json").read_text())
    assert manifest["backend"] == {
        "declared_mode": "REAL_API",
        "verified": False,
        "declared_ref": None,
    }
    assert summary["mode"]["real_mirror_cpu"] == "NOT_ESTABLISHED"
    assert summary["mode"]["gpu_arms"] == "NOT_RUN"
    assert sorted(summary["not_run"]) == [
        "control.restart-recovery",
        "gpu-stub.dense",
        "gpu-stub.objects",
        "gpu-stub.vlm",
    ]


# -- the trace's no-request branch: only an explicit, complete committed set -------

DECLARED = frozenset({"synthetic/scan00.bin", "synthetic/scan01.bin"})


def _skipped(check_id, committed):
    return Check(
        check_id,
        Transport.REAL_API,
        Status.PASS,
        {"committed_at_register": sorted(committed)},
        None,
    )


def _with_skip(committed_upload, committed_commit):
    checks = [c for c in _conforming() if c.id not in {"lifecycle.upload", "lifecycle.commit"}]
    checks.append(_skipped("lifecycle.upload", committed_upload))
    checks.append(_skipped("lifecycle.commit", committed_commit))
    return checks


def test_full_register_skip_agrees_with_the_trace():
    result = compare_trace(
        _with_skip(DECLARED, DECLARED), load_trace(), Transport.REAL_API, declared=DECLARED
    )
    assert result.status is Status.PASS, result


@pytest.mark.parametrize(
    "upload, commit",
    [
        (set(), set()),  # a broken client that claims PASS with no request
        ({"synthetic/scan00.bin"}, DECLARED),  # partial set without an observation
        (DECLARED | {"synthetic/scan02.bin"}, DECLARED),  # more than declared
    ],
)
def test_a_skip_without_the_complete_committed_set_disagrees(upload, commit):
    result = compare_trace(
        _with_skip(upload, commit), load_trace(), Transport.REAL_API, declared=DECLARED
    )
    assert result.status is Status.FAIL
    assert ("lifecycle.upload", "no-observation") in result.disagreements


def test_only_upload_and_commit_have_a_no_request_branch():
    checks = [c for c in _conforming() if c.id != "lifecycle.finalize"]
    checks.append(_skipped("lifecycle.finalize", DECLARED))
    result = compare_trace(checks, load_trace(), Transport.REAL_API, declared=DECLARED)
    assert ("lifecycle.finalize", "no-observation") in result.disagreements


def test_a_cursor_past_2_53_polls_and_records_without_a_receipt_failure(fake, plan_path, tmp_path):
    from hunter_local_ingest.fakeapi import FakeConfig

    out = tmp_path / "runs"
    # retry_finalize forces a second poll whose ``after`` is the large cursor
    with fake(FakeConfig(cursor_offset=2**62, retry_finalize_once=True)) as api:
        code = run_cli(
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
        polls = [p for m, p, _a in api.state.requests if "/events?" in p]
    assert code == 0
    assert any(f"after={2**62 + 1}" in p for p in polls), polls
    run_dir = only_run_dir(out)
    events = read_checks(run_dir)["lifecycle.events"]
    assert events["status"] == "PASS" and events["observed"]["finalize_retries"] == 1
    assert verify_run_integrity(run_dir) == []
