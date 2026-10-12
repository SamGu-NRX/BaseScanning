"""Privacy: the four reviewed counterexamples, end-to-end canaries, the screen.

Each counterexample test reproduces the input the review used at 1df1f62
and asserts the opposite outcome. Absence is checked by parsing every
artifact and inspecting every key and value, not by the screen's regexes,
so a test passes only when the marker is truly gone.
"""

from __future__ import annotations

import json
import sys
import uuid

import pytest
from conftest import FAST, all_strings, artifact_values, canary, only_run_dir, read_checks, run_cli

from hunter_local_ingest import contract, sanitize
from hunter_local_ingest.fakeapi import FakeConfig
from hunter_local_ingest.netpolicy import EndpointError, is_loopback_host, parse_api_base
from hunter_local_ingest.receipts import (
    CHECKS,
    MANIFEST,
    SCREEN_FILE,
    verify_run_integrity,
    write_summary,
)
from hunter_local_ingest.runner import FLAG_KINDS
from hunter_local_ingest.sanitize import (
    redact_argv,
    redact_text,
    safe_failure,
    screen_artifact,
)

# --------------------------------------------------------------------------
# Counterexample 1: shape-valid server ids, paths and key names
# --------------------------------------------------------------------------


def test_ce1_server_ids_paths_and_keys_never_reach_public_views():
    ident, path, key = canary("id"), canary("path"), canary("key")
    declared = frozenset({"synthetic/scan00.bin"})
    finalize = contract.decode_finalize(
        {"status": "processing", "missing": [path], "runId": ident, key: ident}, declared
    )
    commit = contract.decode_commit(
        {"committed": [path], "notFound": [], "mismatch": [], key: "x"},
        ["synthetic/scan00.bin"],
        declared,
    )
    events = contract.decode_events(
        {
            "status": "processing",
            "next": 1,
            key: 1,
            "events": [
                {
                    "seq": 1,
                    "type": ident,
                    "at": "t",
                    key: path,
                    "data": {"runId": ident, "stage": path, key: ident},
                }
            ],
        },
        after=0,
        run_id="run-1",
    )
    result = contract.decode_result(
        {
            "runId": ident,
            "status": "complete",
            "viewsNeeded": [],
            "memberActions": [],
            "outcome": {
                "kind": "eligible",
                "profile": key,
                "message": ident,
                "viewsNeeded": [],
                "reasons": [{"criterion": key, "text": path}],
                key: ident,
            },
            "previewUrl": f"https://storage.example/{ident}",
            key: path,
        }
    )
    for decoded in (finalize, commit, events, result):
        strings = all_strings(decoded.public)
        for marker in (ident, path, key):
            assert not any(marker in s for s in strings), decoded.public
    # disagreements stay diagnosable without the payload
    assert finalize.public["runId"].startswith("id:")
    assert finalize.public["missing"][0].startswith("unexpected-path:")
    assert finalize.public["unexpected_keys"]["count"] == 1
    assert "unexpected-path:committed" in commit.violations
    assert events.public["events"][0]["type"].startswith("unrecognized:")


def test_ce1_end_to_end_canaries_absent_from_every_artifact(fake, plan_path, tmp_path):
    marker = canary("srv")
    out = tmp_path / "runs"
    with fake(FakeConfig(canary=marker)) as api:
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
    run_dir = only_run_dir(out)
    strings = artifact_values(run_dir)
    assert strings, "the run wrote artifacts"
    assert not any(marker in s for s in strings)
    assert marker.encode() not in b"".join(p.read_bytes() for p in run_dir.iterdir())
    # the canaried fake still conforms, so the lifecycle passes: the absence
    # is not an artifact of an early stop
    checks = read_checks(run_dir)
    assert checks["lifecycle.result"]["status"] == "PASS"
    assert code == 0


def test_ce1_unexpected_committed_path_fails_and_stays_out(fake, plan_path, tmp_path):
    marker = canary("cp")
    out = tmp_path / "runs"
    with fake(FakeConfig(canary=marker, commit_extra_path=True)) as api:
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
    run_dir = only_run_dir(out)
    assert code == 1
    assert read_checks(run_dir)["lifecycle.commit"]["status"] == "FAIL"
    assert not any(marker in s for s in artifact_values(run_dir))


# --------------------------------------------------------------------------
# Counterexample 2: separated credential flags in recorded commands
# --------------------------------------------------------------------------

CREDENTIAL_FLAGS = sorted(sanitize._CREDENTIAL_FLAG_NAMES) + [
    "github-token",
    "client_secret",
    "X-Auth-Token",
    "session-cookie",
    "private-key",
]


@pytest.mark.parametrize("flag", CREDENTIAL_FLAGS)
def test_ce2_every_credential_flag_form_is_redacted(flag):
    secret = canary("tok")
    forms = [
        [f"--{flag}", secret],
        [f"--{flag}={secret}"],
        [f"--{flag.upper()}", secret],
        [f"--{flag}", f"'{secret}'"],
        [f'--{flag}="{secret}"'],
        [f"--{flag} {secret}"],  # quoted into one element: one value, never split
        ["run", f"-{flag[0]}", secret],
    ]
    for argv in forms:
        rendered = redact_argv(["run", *argv], FLAG_KINDS)
        assert not any(secret in token for token in rendered), (argv, rendered)


def test_ce2_review_input_no_longer_leaks():
    secret = "CANARY-token-012345abcdef"
    rendered = redact_argv(["hunter-local-ingest", "run", "--token", secret])
    assert secret not in " ".join(rendered)
    assert rendered[-2:] == ["--token", "***"]


def test_ce2_known_flags_render_by_type_and_foreign_values_never_appear():
    argv = [
        "--endpoint",
        "http://127.0.0.1:8000/v1",
        "--plan-file",
        "/Users/x/plan.json",
        "--label",
        "nightly",
        "--backend-mode",
        "REAL_API",
        "--seed",
        "7",
        "--backend-ref",
        "1df1f62c",
        "--client-ref",
        "not-hex!",
    ]
    assert redact_argv(argv, FLAG_KINDS) == [
        "--endpoint",
        "127.0.0.1:8000",
        "--plan-file",
        "<path>",
        "--label",
        "nightly",
        "--backend-mode",
        "REAL_API",
        "--seed",
        "7",
        "--backend-ref",
        "1df1f62c",
        "--client-ref",
        "<invalid>",
    ]
    # a non-loopback endpoint is never recorded
    assert redact_argv(["--endpoint", "https://mirror.example/v1"], FLAG_KINDS) == [
        "--endpoint",
        "<redacted:endpoint>",
    ]


def test_ce2_usage_error_output_never_echoes_values(capsys, plan_path, tmp_path):
    secret = canary("tok")
    with pytest.raises(SystemExit) as exc:
        run_cli(
            [
                "--endpoint",
                "http://127.0.0.1:9/v1",
                "--plan-file",
                str(plan_path),
                "--backend-mode",
                "FAKE_HUNTER",
                "--out",
                str(tmp_path / "o"),
                "--token",
                secret,
            ]
        )
    assert exc.value.code == 2
    err = capsys.readouterr()
    assert secret not in err.out + err.err
    assert "usage:unrecognized-arguments" in err.err
    assert not (tmp_path / "o").exists(), "a usage error writes no receipts"


def test_ce2_invalid_choice_and_bad_endpoint_do_not_echo(capsys, plan_path, tmp_path):
    secret = canary("mode")
    with pytest.raises(SystemExit):
        run_cli(
            [
                "--endpoint",
                "http://127.0.0.1:9/v1",
                "--plan-file",
                str(plan_path),
                "--backend-mode",
                secret,
            ]
        )
    err = capsys.readouterr().err
    assert secret not in err and "usage:invalid-value:--backend-mode" in err
    code = run_cli(
        [
            "--endpoint",
            f"http://u:{secret}@127.0.0.1:9/v1",
            "--plan-file",
            str(plan_path),
            "--backend-mode",
            "FAKE_HUNTER",
            "--out",
            str(tmp_path / "o"),
        ]
    )
    err = capsys.readouterr().err
    assert code == 2 and secret not in err and "endpoint:base_userinfo" in err


def test_ce2_restart_argv_values_stay_out_of_the_manifest(fake, plan_path, tmp_path):
    secret = canary("rs")
    restart = [
        sys.executable,
        "-c",
        "pass",
        "--token",
        secret,
        f"--client-secret={secret}",
        f"--api_key '{secret}'",
        secret,
    ]
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
                "--restart-argv-json",
                json.dumps(restart),
                *FAST,
            ]
        )
    run_dir = only_run_dir(out)
    assert code == 0
    assert read_checks(run_dir)["control.restart-recovery"]["status"] == "PASS"
    manifest = json.loads((run_dir / MANIFEST).read_text())
    assert manifest["restart"]["configured"] is True
    assert not any(secret in s for s in artifact_values(run_dir))


def test_ce2_safe_failure_names_a_category_only():
    secret = canary("f")
    line = safe_failure(
        ["--endpoint", f"https://u:{secret}@10.1.2.3/v1", "--token", secret],
        "endpoint:base_userinfo",
    )
    assert secret not in line and "10.1.2.3" not in line
    assert line.startswith("error: endpoint:base_userinfo")
    # a category that is not fixed text is replaced, never printed
    assert safe_failure([], f"boom {secret}") == "error: error (argv: )"


# --------------------------------------------------------------------------
# Counterexample 3: lookalike loopback hosts
# --------------------------------------------------------------------------

LOOKALIKES = [
    "http://127.0.0.1.evil.com:8000/v1",
    "http://127.0.0.2:8000/v1",
    "http://localhost.evil.com/v1",
    "http://foo.localhost:8000/v1",
    "http://127.0.0.1@evil.com/v1",
]


@pytest.mark.parametrize("url", LOOKALIKES)
def test_ce3_lookalike_loopback_is_refused_everywhere(url, tmp_path):
    with pytest.raises(EndpointError):
        parse_api_base(url)
    assert redact_text(f"endpoint {url} answered") != f"endpoint {url} answered"
    probe = tmp_path / "probe.jsonl"
    probe.write_text(json.dumps({"observed": url}) + "\n")
    assert screen_artifact(str(probe)), url
    assert redact_argv(["--endpoint", url], FLAG_KINDS)[1] in {"<invalid>", "<redacted:endpoint>"}


def test_ce3_exact_loopback_hosts_pass():
    for host in ("127.0.0.1", "::1", "[::1]", "localhost", "LOCALHOST"):
        assert is_loopback_host(host)
    for url in ("http://127.0.0.1:8000/v1", "http://[::1]:8000/v1", "http://localhost/v1"):
        parse_api_base(url)
        assert redact_text(url) == url
    assert parse_api_base("https://api.example/v1").label == "<redacted:endpoint>"


# --------------------------------------------------------------------------
# Counterexample 4: missing mandatory artifacts
# --------------------------------------------------------------------------


def test_ce4_summary_on_empty_run_dir_is_not_clean(tmp_path):
    from hunter_local_ingest.export import LEAD_QUESTIONS, ExportContext

    report = write_summary(
        tmp_path,
        "0123456789abcdef",
        [],
        declared_backend="FAKE_HUNTER",
        lead_questions=LEAD_QUESTIONS,
        ctx=ExportContext(),
    )
    assert not report.receipts_ok
    assert set(report.missing) == {MANIFEST, CHECKS}
    assert report.verdict == "BLOCKED"
    # no empty stand-ins were created
    assert not (tmp_path / MANIFEST).exists() and not (tmp_path / CHECKS).exists()
    screen = json.loads((tmp_path / SCREEN_FILE).read_text())
    assert screen["clean"] is False and set(screen["missing"]) == {MANIFEST, CHECKS}
    problems = verify_run_integrity(tmp_path)
    assert f"{MANIFEST}: missing-artifact" in problems
    assert f"{CHECKS}: missing-artifact" in problems


def test_ce4_summary_refuses_rewrite(tmp_path):
    from hunter_local_ingest.export import LEAD_QUESTIONS, ExportContext

    write_summary(
        tmp_path,
        "0123456789abcdef",
        [],
        declared_backend="FAKE_HUNTER",
        lead_questions=LEAD_QUESTIONS,
        ctx=ExportContext(),
    )
    with pytest.raises(FileExistsError):
        write_summary(
            tmp_path,
            "0123456789abcdef",
            [],
            declared_backend="FAKE_HUNTER",
            lead_questions=LEAD_QUESTIONS,
            ctx=ExportContext(),
        )


def test_ce4_each_deleted_artifact_is_named(fake, plan_path, tmp_path):
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
    for name in (
        "run-manifest.json",
        "checks.jsonl",
        "summary.json",
        "privacy-screen.json",
        "privacy-chain.jsonl",
    ):
        moved = run_dir / name
        hidden = tmp_path / f"hidden-{name}"
        moved.rename(hidden)
        assert f"{name}: missing-artifact" in verify_run_integrity(run_dir), name
        hidden.rename(moved)


# --------------------------------------------------------------------------
# The pattern screen (second layer): categories, mutated variants, clean text
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("category", "template", "vanish_template"),
    [
        ("bearer-token", "Authorization: Bearer {c}", "{c}"),
        ("credential-kv", "api_key: {c}", "{c}"),
        ("credential-kv", "password={c}", "{c}"),
        ("credential-kv", 'X-Synthetic-Signature: "{c}"', "{c}"),
        ("url-userinfo", "endpoint https://svc:{c}@mirror.internal.example/v1", "{c}"),
        ("signed-url", "https://127.0.0.1:9443/storage/u?X-Signature={c}", "{c}"),
        ("private-endpoint", "mirror answered https://mirror.internal.example/v1/c?x={c}", "{c}"),
        ("private-hostname", "backend at {c}.internal refused", "{c}.internal"),
        ("private-hostname", "host 10.201.3.4 refused", "10.201.3.4"),
        ("key-material", "-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----"),
        ("key-material", "AKIA" + "A" * 16, "AKIA" + "A" * 16),
        ("key-material", "ghp_{c}", "ghp_{c}"),
        ("source-snippet", "error in rules/clearance.py:412", "clearance.py:412"),
        ("source-snippet", "Traceback (most recent call last):", "Traceback (most recent"),
        ("source-snippet", "read from house-scanning-server", "house-scanning-server"),
        ("source-snippet", "loaded /home/agent/.env", "/home/agent/.env"),
        ("non-ascii-or-control", "status\x1b[31mred\x00", "\x1b"),
    ],
)
def test_screen_category_detected_and_redacted(category, template, vanish_template, tmp_path):
    marker = canary("x")
    payload = template.replace("{c}", marker)
    vanish = vanish_template.replace("{c}", marker)
    artifact = tmp_path / "probe.jsonl"
    artifact.write_text(payload + "\n", encoding="utf-8")
    findings = screen_artifact(str(artifact))
    assert any(category in f for f in findings), findings
    assert marker not in " ".join(findings)
    redacted = redact_text(payload)
    assert vanish not in redacted
    assert f"<redacted:{category}>" in redacted
    assert redact_text(redacted) == redacted


def test_screen_mutated_variants_still_caught(tmp_path):
    variants = {
        "bearer-case-toggle": f"bEaReR {canary('b')}",
        "bearer-tab-separator": f"Bearer\t{canary('b')}",
        "userinfo-percent-encoded": f"https://user%2Fx:{canary('pw')}%21@127.0.0.1:9443/v1",
        "signed-param-lowercase": f"http://127.0.0.1:9000/s?x-signature={canary('s')}",
        "signed-param-amz": f"http://127.0.0.1:9000/s?X-Amz-Signature={canary('s')}",
        "cred-kv-colon-form": f"'password' : '{canary('pw2')}'",
        "cred-kv-flag-space-form": f"--api_key {canary('key2')}",
    }
    for name, payload in variants.items():
        artifact = tmp_path / f"{name}.jsonl"
        artifact.write_text(json.dumps({"observed": payload}) + "\n", encoding="utf-8")
        assert screen_artifact(str(artifact)), name


def test_screen_unfolds_single_json_escapes(tmp_path):
    """On-disk ``\\u0042earer`` parses to ``Bearer``: a real smuggling vector."""
    token = canary("esc")
    artifact = tmp_path / "escaped.jsonl"
    artifact.write_text('{"observed": "\\u0042earer ' + token + '"}\n', encoding="utf-8")
    assert json.loads(artifact.read_text())["observed"] == f"Bearer {token}"
    assert any("bearer-token" in f for f in screen_artifact(str(artifact)))


def test_screen_undecodable_and_missing(tmp_path):
    artifact = tmp_path / "binary.jsonl"
    artifact.write_bytes(b"\xff\xfe\x00bad")
    assert "undecodable" in screen_artifact(str(artifact))[0]
    with pytest.raises(FileNotFoundError):
        screen_artifact(str(tmp_path / "nope.jsonl"))


def test_clean_receipt_content_passes(tmp_path):
    honest = {
        "status": "complete",
        "captureId": "id:1a2b3c4d5e6f",
        "seq": 12,
        "body_sha256": "ab" * 6,
        "committed": ["synthetic/scan00.bin"],
        "url": "http://127.0.0.1:8000/v1",
    }
    artifact = tmp_path / "honest.jsonl"
    artifact.write_text(json.dumps(honest, sort_keys=True) + "\n", encoding="utf-8")
    assert screen_artifact(str(artifact)) == []


def test_shipped_constants_pass_the_screen():
    from hunter_local_ingest.export import LEAD_QUESTIONS, ORIGIN_NOTE, SANITIZATION_NOTE
    from hunter_local_ingest.scenarios.oracles import ORACLES

    texts = [ORIGIN_NOTE, SANITIZATION_NOTE, *LEAD_QUESTIONS]
    texts += [text for pair in ORACLES.values() for text in pair]
    for text in texts:
        assert redact_text(text) == text, text


def test_findings_never_echo_material(tmp_path):
    marker = canary("echo")
    artifact = tmp_path / "x.jsonl"
    artifact.write_text(f"password={marker}\n")
    assert marker not in json.dumps(screen_artifact(str(artifact)))


# --------------------------------------------------------------------------
# --api-token on the wire: API routes only, never the proxy, storage or receipts
# --------------------------------------------------------------------------


def test_api_token_is_sent_only_on_direct_api_requests(fake, plan_path, tmp_path, capsys):
    token = f"synthetic-{uuid.uuid4().hex}"
    out = tmp_path / "runs"
    with fake(FakeConfig(require_token=token)) as api:
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
                "--with-faults",
                "--api-token",
                token,
                *FAST,
            ]
        )
        requests = list(api.state.requests)
        api_auth = list(api.state.api_auth)
    run_dir = only_run_dir(out)
    rows = read_checks(run_dir)
    # the direct lifecycle authenticated; the proxied fault round did not
    assert rows["lifecycle.result"]["status"] == "PASS"
    assert rows["fault.lifecycle.create"]["observed"]["http"]["http_status"] == 401
    assert set(api_auth) == {f"Bearer {token}", None}
    assert not any(has_auth for method, _p, has_auth in requests if method == "PUT")
    assert code == 1  # the unauthenticated fault round fails by design
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err
    assert token.encode() not in b"".join(p.read_bytes() for p in run_dir.iterdir())
    assert not any(token in s for s in artifact_values(run_dir))


def test_api_token_never_reaches_a_traversal_destination(fake, plan_path, tmp_path):
    token = f"synthetic-{uuid.uuid4().hex}"
    with fake(FakeConfig(capture_id_override="../outside")) as api:
        run_cli(
            [
                "--endpoint",
                api.base_url,
                "--plan-file",
                str(plan_path),
                "--out",
                str(tmp_path / "runs"),
                "--backend-mode",
                "FAKE_HUNTER",
                "--api-token",
                token,
                *FAST,
            ]
        )
        requests = list(api.state.requests)
    assert requests, "health and create were sent"
    assert not any(
        "outside" in p or "%2F" in p.upper() or "%5C" in p.upper() for _m, p, _a in requests
    )
