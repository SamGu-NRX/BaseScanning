"""The API credential interface: an explicit flag naming a fixed environment variable.

Every token here is a fresh synthetic value injected with monkeypatch, so it
is removed when the test ends; no real credential and no user environment
value is ever read. Absence is checked over raw bytes and parsed values of
every artifact, and over everything the runner printed.
"""

from __future__ import annotations

import json
import sys
import uuid

import pytest
from conftest import FAST, artifact_values, only_run_dir, read_checks, run_cli

from hunter_local_ingest import runner
from hunter_local_ingest.fakeapi import FakeConfig
from hunter_local_ingest.scenarios.oracles import FAULTS, LIFECYCLE

ENV = "HUNTER_API_TOKEN"
FAULT_IDS = [f"fault.{i}" for i in LIFECYCLE] + list(FAULTS)


def _token() -> str:
    return f"synthetic-{uuid.uuid4().hex}"


def _argv(api_url: str, plan_path, out, *extra: str) -> list[str]:
    return [
        "--endpoint",
        api_url,
        "--plan-file",
        str(plan_path),
        "--out",
        str(out),
        "--backend-mode",
        "FAKE_HUNTER",
        *FAST,
        *extra,
    ]


def _no_leak(token: str, run_dir, captured) -> None:
    assert token not in captured.out + captured.err
    assert token.encode() not in b"".join(p.read_bytes() for p in run_dir.iterdir())
    assert not any(token in s for s in artifact_values(run_dir))


def test_the_env_flag_name_is_a_package_constant():
    assert runner.API_TOKEN_ENV == ENV
    assert runner.FLAG_KINDS["--api-token-env"] == "flag"
    assert "--api-token" not in runner.FLAG_KINDS


def test_token_from_the_named_env_reaches_api_routes_only(
    fake, plan_path, tmp_path, capsys, monkeypatch
):
    token = _token()
    monkeypatch.setenv(ENV, token)
    out = tmp_path / "runs"
    with fake(FakeConfig(require_token=token)) as api:
        code = run_cli(_argv(api.base_url, plan_path, out, "--api-token-env"))
        api_auth = set(api.state.api_auth)
        puts = [r for r in api.state.requests if r[0] == "PUT"]
    assert code == 0
    assert api_auth == {f"Bearer {token}"}
    assert puts and not any(has_auth for _m, _p, has_auth in puts)
    run_dir = only_run_dir(out)
    command = json.loads((run_dir / "run-manifest.json").read_text())["command"]
    # the command records the fixed public flag and nothing after it
    assert command.count("--api-token-env") == 1 and "***" not in command
    _no_leak(token, run_dir, capsys.readouterr())


def test_without_the_flag_the_env_is_never_read(fake, plan_path, tmp_path, monkeypatch):
    token = _token()
    monkeypatch.setenv(ENV, token)
    with fake(FakeConfig(require_token=token)) as api:
        code = run_cli(_argv(api.base_url, plan_path, tmp_path / "runs"))
        api_auth = set(api.state.api_auth)
    assert api_auth == {None}
    assert code == 1  # the protected endpoint refuses an unauthenticated create


@pytest.mark.parametrize(
    "value, category",
    [
        (None, "auth_credential_unavailable"),
        ("", "auth_credential_unavailable"),
        ("has space CANARY-env-0123", "auth_credential_malformed"),
        ("line\nbreak-CANARY-env-0123", "auth_credential_malformed"),
    ],
)
def test_a_missing_or_malformed_env_token_stops_before_any_directory_or_request(
    fake, plan_path, tmp_path, capsys, monkeypatch, value, category
):
    if value is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, value)
    out = tmp_path / "runs"
    with fake() as api:
        code = run_cli(_argv(api.base_url, plan_path, out, "--api-token-env"))
        requests = list(api.state.requests)
    captured = capsys.readouterr()
    assert code == 2
    assert category in captured.err
    assert "CANARY-env" not in captured.out + captured.err
    assert requests == [] and not out.exists()


@pytest.mark.parametrize(
    "form",
    [
        ["--api-token", "CANARY-retired-0123"],
        ["--api-token=CANARY-retired-0123"],
        ["--api-token"],
        ["--api-tok", "CANARY-retired-0123"],
        ["--api-token-env=CANARY-retired-0123"],
    ],
)
def test_the_retired_value_flag_is_refused_without_echo(fake, plan_path, tmp_path, capsys, form):
    out = tmp_path / "runs"
    with fake() as api:
        with pytest.raises(SystemExit) as exc:
            run_cli(_argv(api.base_url, plan_path, out, *form))
        requests = list(api.state.requests)
    captured = capsys.readouterr()
    assert exc.value.code == 2
    assert "CANARY-retired" not in captured.out + captured.err
    assert requests == [] and not out.exists()
    if form[0].startswith("--api-token") and not form[0].startswith("--api-token-env"):
        assert "usage:retired-flag:--api-token" in captured.err


def test_the_token_is_not_inherited_by_the_restart_command(fake, plan_path, tmp_path, monkeypatch):
    token = _token()
    monkeypatch.setenv(ENV, token)
    probe = tmp_path / "restart-saw-token.txt"
    restart = [
        sys.executable,
        "-c",
        f"import os; open({str(probe)!r}, 'w').write(str({ENV!r} in os.environ))",
    ]
    with fake(FakeConfig(require_token=token)) as api:
        code = run_cli(
            _argv(
                api.base_url,
                plan_path,
                tmp_path / "runs",
                "--api-token-env",
                "--restart-argv-json",
                json.dumps(restart),
            )
        )
    assert code == 0
    assert probe.read_text() == "False"


# -- decision 2: no credential on the fault proxy; its arm is BLOCKED, not FAIL ----


def test_a_token_protected_fault_round_is_blocked_with_a_fixed_reason(
    fake, plan_path, tmp_path, capsys, monkeypatch
):
    token = _token()
    monkeypatch.setenv(ENV, token)
    out = tmp_path / "runs"
    with fake(FakeConfig(require_token=token)) as api:
        code = run_cli(_argv(api.base_url, plan_path, out, "--api-token-env", "--with-faults"))
        api_auth = list(api.state.api_auth)
        puts = [r for r in api.state.requests if r[0] == "PUT"]
    run_dir = only_run_dir(out)
    rows = read_checks(run_dir)
    assert code == 4
    assert all(rows[i]["status"] == "PASS" for i in LIFECYCLE), "authenticated baseline"
    for check_id in FAULT_IDS:
        assert rows[check_id]["status"] == "BLOCKED", check_id
        assert rows[check_id]["observed"]["note"] == "auth_fault_proxy_not_authorized"
    # no unauthenticated request reached the protected API through any proxy
    assert set(api_auth) == {f"Bearer {token}"}
    assert puts and not any(has_auth for _m, _p, has_auth in puts)
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["verdict"] == "BLOCKED" and not summary["failed"]
    _no_leak(token, run_dir, capsys.readouterr())


def test_without_a_token_the_fault_campaign_still_runs(fake, plan_path, tmp_path):
    out = tmp_path / "runs"
    with fake() as api:
        code = run_cli(_argv(api.base_url, plan_path, out, "--with-faults"))
    rows = read_checks(only_run_dir(out))
    assert code == 0
    assert all(rows[i]["status"] == "PASS" for i in FAULT_IDS)


# -- the whole token must be a b64token: no trailing newline slips through -------

MALFORMED_SUFFIXES = ["\n", "\r", "\r\n", " ", "\t", "\x1b"]


@pytest.mark.parametrize(
    "token",
    [f"CANARY-bearer-0123{s}" for s in MALFORMED_SUFFIXES]
    + ["\tCANARY-bearer-0123", "CANARY-bearer\n0123", "CANARY bearer-0123"],
)
def test_control_characters_and_spaces_are_refused_before_any_directory(
    fake, plan_path, tmp_path, capsys, monkeypatch, token
):
    monkeypatch.setenv(ENV, token)
    out = tmp_path / "runs"
    with fake() as api:
        code = run_cli(_argv(api.base_url, plan_path, out, "--api-token-env"))
        requests = list(api.state.requests)
    captured = capsys.readouterr()
    assert code == 2
    assert "auth_credential_malformed" in captured.err
    assert "CANARY-bearer" not in captured.out + captured.err
    assert requests == [] and not out.exists()


@pytest.mark.parametrize("suffix", MALFORMED_SUFFIXES)
def test_the_client_constructor_rejects_the_whole_token(suffix):
    from hunter_local_ingest.http_client import DirectHttpClient
    from hunter_local_ingest.netpolicy import EndpointError

    with pytest.raises(EndpointError) as exc:
        DirectHttpClient("http://127.0.0.1:9/v1", api_token="abc" + suffix)
    assert exc.value.code == "auth_credential_malformed"
    DirectHttpClient("http://127.0.0.1:9/v1", api_token="abc-._~+/==")  # valid baseline
