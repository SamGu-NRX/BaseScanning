"""Runner-level credential wiring: the --api-token-env flag.

The token comes only from the fixed ``HUNTER_API_TOKEN`` variable, and only
when ``--api-token-env`` is given; each test injects a synthetic value with
monkeypatch, which removes it when the test ends.

The client's own credential behavior (bearer on API routes only, storage
PUTs clean, malformed refused, server-minted ids never credentialed) is
proven in test_lifecycle. These tests pin the runner seam that feeds it:
the flag parses, a malformed token is refused before any run directory
exists with a redacted one-line failure, the token reaches every client
bound for the declared endpoint, and it reaches nothing else — not the
fault round's local proxy, and no receipt artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

from hunter_local_ingest import runner
from hunter_local_ingest.fakeapi import FakeApi, FakeConfig
from hunter_local_ingest.sanitize import redact_argv

TOKEN = "synthetic-b64token-_~="

FAST = [
    "--poll-interval-s",
    "0.01",
    "--events-wait-s",
    "0",
    "--retry-backoff-s",
    "0",
    "--result-budget-s",
    "5",
]


def _argv(plan_path: Path, url: str, out: Path, *extra: str) -> list[str]:
    return [
        "--endpoint",
        url,
        "--plan-file",
        str(plan_path),
        "--out",
        str(out),
        "--label",
        "authwiring",
        "--backend-mode",
        "FAKE_HUNTER",
        *FAST,
        *extra,
    ]


def _only_run_dir(out: Path) -> Path:
    dirs = [p for p in out.iterdir() if p.is_dir()]
    assert len(dirs) == 1, dirs
    return dirs[0]


def _all_strings(value) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            out.append(str(key))
            out.extend(_all_strings(item))
    elif isinstance(value, list):
        for item in value:
            out.extend(_all_strings(item))
    return out


def _artifact_strings(run_dir: Path) -> list[str]:
    strings: list[str] = []
    for path in run_dir.iterdir():
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            for line in text.splitlines():
                strings.extend(_all_strings(json.loads(line)))
        else:
            strings.extend(_all_strings(json.loads(text)))
    return strings


def test_token_carried_on_api_routes_and_redacted_in_receipts(plan_path, tmp_path, monkeypatch):
    monkeypatch.setenv(runner.API_TOKEN_ENV, TOKEN)
    out = tmp_path / "out"
    with FakeApi() as api:
        code = runner.run(_argv(plan_path, api.base_url, out, "--api-token-env"))
        assert code == runner.EXIT_PASS
        api_auth = list(api.state.api_auth)
        put_headers = list(api.state.put_headers)
    assert api_auth, "the runner must send API requests"
    assert set(api_auth) == {f"Bearer {TOKEN}"}
    assert all("authorization" not in {k.lower() for k in h} for h in put_headers)

    run_dir = _only_run_dir(out)
    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    command = manifest["command"]
    assert command.count("--api-token-env") == 1 and "***" not in command
    assert TOKEN not in _all_strings(manifest)
    assert TOKEN not in _artifact_strings(run_dir)


def test_without_the_flag_a_token_requiring_endpoint_fails(plan_path, tmp_path):
    with FakeApi(FakeConfig(require_token=TOKEN)) as api:
        code = runner.run(_argv(plan_path, api.base_url, tmp_path / "out"))
    assert code == runner.EXIT_FAIL


def test_malformed_token_refused_before_any_run_directory(plan_path, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(runner.API_TOKEN_ENV, "bad token")
    out = tmp_path / "out"
    code = runner.run(_argv(plan_path, "http://127.0.0.1:9/v1", out, "--api-token-env"))
    assert code == runner.EXIT_USAGE
    err = capsys.readouterr().err
    assert "auth_credential_malformed" in err
    assert "bad token" not in err
    assert not out.exists() or not list(out.iterdir())


def test_token_is_not_handed_to_the_fault_proxy(plan_path, tmp_path, monkeypatch):
    seen: list[dict] = []
    real = runner.DirectHttpClient

    class Spy(real):
        def __init__(self, base_url, **kw) -> None:  # noqa: ANN003
            seen.append({"base": base_url, "token": kw.get("api_token")})
            super().__init__(base_url, **kw)

    monkeypatch.setattr(runner, "DirectHttpClient", Spy)
    monkeypatch.setenv(runner.API_TOKEN_ENV, TOKEN)
    out = tmp_path / "out"
    with FakeApi() as api:
        code = runner.run(_argv(plan_path, api.base_url, out, "--with-faults", "--api-token-env"))
    # the fault arm is BLOCKED rather than run unauthenticated (exit 4)
    assert code == runner.EXIT_BLOCKED
    endpoint = {s["token"] for s in seen if s["base"] == api.base_url}
    others = [s for s in seen if s["base"] != api.base_url]
    assert endpoint == {TOKEN}
    assert others == [], "no client is built for the fault proxy when a token is in use"


def test_redact_argv_holds_for_the_declared_flag_in_both_forms():
    for argv in (
        ["--api-token", TOKEN, "--label", "run"],
        [f"--api-token={TOKEN}", "--label", "run"],
    ):
        redacted = redact_argv(argv, runner.FLAG_KINDS)
        assert all(TOKEN not in token for token in redacted)
        assert any("***" in token for token in redacted)
