"""Privacy tests: the artifact-privacy screen and command redaction.

All canary material below is synthetic and assembled at test runtime;
nothing here is a real credential. The matrix covers:

  - the exact sibling seam (screen_artifact / redact_command signatures)
  - every forbidden category, with independent MUTATED variants (case
    toggles, percent-encoding, JSON escapes, renamed signature params) so a
    pattern-brittle screen cannot pass
  - clean honest receipt content that must pass untouched, proving the
    screen is not over-blocking and not a no-op
"""

from __future__ import annotations

import inspect
import json
import uuid

import pytest

from hunter_local_ingest import sanitize
from hunter_local_ingest.sanitize import redact_command, redact_text, screen_artifact


def canary(tag: str) -> str:
    """A synthetic, runtime-assembled marker that reads as a secret."""
    return f"CANARY-{tag}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# the seam contract
# ---------------------------------------------------------------------------


def test_seam_exports_exact_signatures():
    assert list(inspect.signature(sanitize.screen_artifact).parameters) == ["path"]
    assert list(inspect.signature(sanitize.redact_command).parameters) == ["argv"]


def test_seam_findings_are_strings_and_clean_means_empty(tmp_path):
    dirty = tmp_path / "dirty.jsonl"
    token = canary("bearer")
    dirty.write_text(json.dumps({"observed": f"Authorization: Bearer {token}"}) + "\n")
    findings = screen_artifact(str(dirty))
    assert findings and all(isinstance(f, str) for f in findings)
    clean = tmp_path / "clean.jsonl"
    clean.write_text(json.dumps({"observed": "statuses complete failed"}) + "\n")
    assert screen_artifact(str(clean)) == []


# ---------------------------------------------------------------------------
# redact_command
# ---------------------------------------------------------------------------

CLEAN_ARGV = [
    "hunter-local-ingest",
    "run",
    "--endpoint",
    "http://127.0.0.1:8000/v1",
    "--backend-mode",
    "REAL_MIRROR_CPU",
    "--seed",
    "7",
]


def test_redact_command_clean_command_unchanged():
    assert redact_command(CLEAN_ARGV) == (
        "hunter-local-ingest run --endpoint http://127.0.0.1:8000/v1 "
        "--backend-mode REAL_MIRROR_CPU --seed 7"
    )


def test_redact_command_endpoint_userinfo_redacted():
    user, pw = canary("user"), canary("pw")
    out = redact_command(["--endpoint", f"https://{user}:{pw}@127.0.0.1:9443/v1"])
    assert pw not in out and user not in out
    assert "<redacted:url-userinfo>" in out


def test_redact_command_endpoint_signed_query_redacted():
    sig = canary("sig")
    out = redact_command(
        ["--endpoint", f"http://127.0.0.1:9000/storage/u.bin?X-Signature={sig}"]
    )
    assert sig not in out
    assert "<redacted:signed-url>" in out


def test_redact_command_non_loopback_endpoint_redacted_wholly():
    out = redact_command(["--endpoint", "https://mirror.internal.example/v1/captures"])
    assert "mirror.internal.example" not in out
    assert "<redacted:private-endpoint>" in out


def test_redact_command_credential_flag_space_form():
    key = canary("apikey")
    assert redact_command(["--api-key", key]) == "--api-key ***"
    assert key not in redact_command(["--api-key", key])


def test_redact_command_credential_flag_eq_form():
    key = canary("apikey")
    out = redact_command([f"--api-key={key}"])
    assert out == "--api-key=***"
    assert key not in out


def test_redact_command_credential_flag_case_and_underscore_variants():
    token = canary("tok")
    assert redact_command(["--API_KEY", token]).endswith(" ***")
    assert redact_command([f"--TOKEN={token}"]) == "--TOKEN=***"
    assert token not in redact_command(["--API_KEY", token, f"--TOKEN={token}"])


def test_redact_command_restart_command_interior_token():
    token = canary("tok")
    out = redact_command(
        ["--restart-command", f"systemctl restart hunter --auth-token {token}"]
    )
    assert token not in out
    assert "--restart-command" in out


def test_redact_command_flag_at_end_without_value():
    assert redact_command(["--api-key"]) == "--api-key"


# ---------------------------------------------------------------------------
# screen_artifact: forbidden categories + redact_text round trip
# ---------------------------------------------------------------------------


def _bearer_text(token: str) -> str:
    return f"Authorization: Bearer {token}"


@pytest.mark.parametrize(
    ("category", "template", "vanish_template"),
    [
        # (category, payload template, substring that must vanish after
        # redaction). {c} marks where the runtime canary rides *inside* the
        # matched material; entries without {c} match structural markers.
        ("bearer-token", "Authorization: Bearer {c}", "{c}"),
        ("credential-kv", "api_key: {c}", "{c}"),
        ("credential-kv", "password={c}", "{c}"),
        ("credential-kv", 'X-Synthetic-Signature: "{c}"', "{c}"),
        ("url-userinfo", "endpoint https://svc:{c}@mirror.internal.example/v1", "{c}"),
        ("signed-url", "https://127.0.0.1:9443/storage/u?X-Signature={c}", "{c}"),
        ("private-endpoint", "mirror answered https://mirror.internal.example/v1/c?x={c}", "{c}"),
        ("private-hostname", "backend at {c}.internal refused", "{c}.internal"),
        ("private-hostname", "host 10.201.3.4 refused", "10.201.3.4"),
        ("private-hostname", "relay 192.168.14.22 down", "192.168.14.22"),
        ("key-material", "-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----"),
        ("key-material", "AKIA" + "A" * 16, "AKIA" + "A" * 16),
        ("key-material", "ghp_{c}", "ghp_{c}"),
        ("key-material", "eyJhbGciOiJFUzI1NiJ9.{c}.{c}", "eyJhbGciOiJFUzI1NiJ9.{c}"),
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
    # findings never echo the matched material
    assert marker not in " ".join(findings)

    redacted = redact_text(payload)
    assert vanish not in redacted
    assert f"<redacted:{category}>" in redacted
    # redaction is idempotent and its output re-screens clean
    assert redact_text(redacted) == redacted


def test_screen_mutated_variants_still_caught(tmp_path):
    """Mutation controls: no variant form may slip past the screen."""
    token = canary("bearer")
    pw = canary("pw")
    sig = canary("sig")
    variants = {
        "bearer-case-toggle": f"bEaReR {token}",
        "bearer-tab-separator": f"Bearer\t{token}",
        "userinfo-percent-encoded": f"https://user%2Fx:{pw}%21@127.0.0.1:9443/v1",
        "signed-param-lowercase": f"http://127.0.0.1:9000/s?x-signature={sig}",
        "signed-param-amz": f"http://127.0.0.1:9000/s?X-Amz-Signature={sig}",
        "cred-kv-colon-form": f"'password' : '{canary('pw2')}'",
        "cred-kv-flag-space-form": f"--api_key {canary('key2')}",
    }
    for name, payload in variants.items():
        artifact = tmp_path / "variants.jsonl"
        artifact.write_text(json.dumps({"observed": payload}) + "\n", encoding="utf-8")
        findings = screen_artifact(str(artifact))
        assert findings, f"mutation control {name} was not caught: {payload!r}"
        # the JSON-escaped on-disk form must also be caught
        assert token not in " ".join(findings)


def test_screen_json_escaped_forms_unfolded(tmp_path):
    """Single-escape \\uXXXX forms on disk (which parse to real text) must
    not hide forbidden material."""
    token = canary("bearer")
    # on-disk bytes: {"observed": "\u0042earer <token>"} — a single backslash
    artifact = tmp_path / "escaped.jsonl"
    artifact.write_text('{"observed": "\\u0042earer ' + token + '"}\n', encoding="utf-8")
    assert json.loads(artifact.read_text(encoding="utf-8"))["observed"] == (
        f"Bearer {token}"
    )  # the escape is a real smuggling vector, not decoration
    findings = screen_artifact(str(artifact))
    assert any("bearer-token" in f for f in findings), findings


def test_screen_undecodable_artifact_is_a_finding(tmp_path):
    artifact = tmp_path / "binary.jsonl"
    artifact.write_bytes(b"\xff\xfe\x00bad")
    findings = screen_artifact(str(artifact))
    assert findings and "undecodable" in findings[0]


def test_screen_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        screen_artifact(str(tmp_path / "nope.jsonl"))


def test_screen_truncates_pathological_files(tmp_path):
    artifact = tmp_path / "many.jsonl"
    token = canary("flood")
    artifact.write_text(
        "\n".join(f"password={token}" for _ in range(500)), encoding="utf-8"
    )
    findings = screen_artifact(str(artifact))
    assert len(findings) <= sanitize.MAX_FINDINGS + 1
    assert "truncated" in findings[-1]


# ---------------------------------------------------------------------------
# clean controls: honest receipt content must pass untouched
# ---------------------------------------------------------------------------


def test_clean_receipt_content_passes(tmp_path):
    honest = {
        "status": "complete",
        "captureId": "cap-1a2b3c4d",
        "runId": "run-9f8e7d6c",
        "seq": 12,
        "at": "2026-10-12T02:23:36Z",
        "body_sha256": "ab" * 32,
        "md5_b64": "dGVzdDEyMzQ1Njc4OTA9",
        "committed": ["frames/0001/keyframe.bin", "depth/0002.bin"],
        "packet_id": "pkt_4ckQ9tZr0S",
    }
    artifact = tmp_path / "honest.jsonl"
    artifact.write_text(
        "\n".join(json.dumps(honest, sort_keys=True) for _ in range(3)) + "\n",
        encoding="utf-8",
    )
    assert screen_artifact(str(artifact)) == []
    assert redact_text(json.dumps(honest, sort_keys=True)) == json.dumps(
        honest, sort_keys=True
    )


def test_clean_loopback_urls_pass():
    text = (
        "endpoint http://127.0.0.1:8000/v1 unreachable (TCP) within 10s; "
        "also tried https://localhost:9443 and http://[::1]:8000/v1"
    )
    assert redact_text(text) == text


def test_shipped_prose_passes_unchanged():
    """The runner's own origin note and lead questions must never be flagged."""
    from hunter_local_ingest.runner import LEAD_QUESTIONS, ORIGIN_NOTE

    for prose in [ORIGIN_NOTE, *LEAD_QUESTIONS]:
        assert redact_text(prose) == prose


def test_selfcheck_disclaimer_passes_unchanged():
    # the backend_mode string selfcheck records for its in-process fake
    disclaimer = "plumbing-fake (self-check only; never evidence)"
    assert redact_text(disclaimer) == disclaimer


# ---------------------------------------------------------------------------
# observations.py: per-key value validation (recon gap d/§4)
# ---------------------------------------------------------------------------

from hunter_local_ingest.observations import Observation, sanitize_body  # noqa: E402


def test_allowed_key_smuggled_values_are_replaced():
    marker = canary("x")
    body = {
        "status": f"complete?next=https://127.0.0.1:9/x?X-Signature={marker}",
        "runId": f"run-1; password={canary('pw')}",
        "decision": f"Bearer {canary('bearer')}",
        "code": "ok\x1b[31m",
    }
    safe = sanitize_body(body)
    assert safe["status"] == "<unsafe>"
    assert safe["runId"] == "<unsafe>"
    assert safe["decision"] == "<unsafe>"
    assert safe["code"] == "<unsafe>"
    assert marker not in json.dumps(safe)


def test_allowed_key_valid_values_pass_through():
    body = {
        "status": "manual_review",
        "captureId": "cap-1a2b3c4d",
        "runId": "run-9f8e7d6c",
        "seq": 4,
        "next": 7,
        "etaS": 30,
        "type": "verdict_ready",
        "code": "result_not_ready",
        "stage": "upload",
        "kind": "eligible",
        "decision": "pass",
        "schema_version": "0.4",
        "at": "2026-10-12T02:53:23Z",
    }
    assert sanitize_body(body) == body


def test_value_type_gates():
    assert sanitize_body({"status": True})["status"] == "<unsafe>"
    assert sanitize_body({"seq": "12"})["seq"] == "<unsafe>"  # string, not int
    assert sanitize_body({"seq": 10**12})["seq"] == "<unsafe>"  # out of range
    assert sanitize_body({"etaS": 2.5})["etaS"] == "<unsafe>"  # float, not int
    assert sanitize_body({"status": ["complete"]})["status"] == "<name-only>"
    # "next" is either an int cursor or a retry directive word
    assert sanitize_body({"next": "retry_finalize"})["next"] == "retry_finalize"


def test_overlong_vocab_value_rejected_but_64_char_vocab_passes():
    assert sanitize_body({"status": "a" * 65})["status"] == "<unsafe>"
    assert sanitize_body({"status": "a" * 64})["status"] == "a" * 64


def test_path_list_items_are_validated_per_item():
    safe = sanitize_body(
        {
            "committed": [
                "frames/0001/keyframe.bin",
                "/srv/hss-baseline/private/rules/clearance.py",
                "../etc/passwd",
                "C:\\\\Users\\\\x",
                "a" * 129,
                None,
            ]
        }
    )
    assert safe["committed"][0] == "frames/0001/keyframe.bin"
    assert all(item == "<unsafe>" for item in safe["committed"][1:])


def test_events_reduce_to_validated_scalars_only():
    marker = canary("x")
    body = {
        "events": [
            {
                "seq": 1,
                "type": "verdict_ready",
                "at": "2026-10-12T00:00:00Z",
                "runId": f"run-1?leak={marker}",
                "signedUrl": f"https://127.0.0.1:9443/s?X-Signature={marker}",
                "blob": {"deep": "nested"},
            },
            "not-a-dict",
        ]
    }
    safe = sanitize_body(body)["events"]
    assert safe[0] == {
        "seq": 1,
        "type": "verdict_ready",
        "at": "2026-10-12T00:00:00Z",
        "runId": "<unsafe>",
    }
    assert safe[1] == "<non-dict>"
    assert marker not in json.dumps(safe)


def test_field_names_and_detail_are_screened():
    marker = canary("x")
    body = json.dumps(
        {"status": "complete", f"x?X-Signature={marker}": "v"}, ensure_ascii=True
    ).encode()
    obs = Observation.from_http(
        200,
        body,
        5,
        detail=f"transport error: https://svc:{marker}@10.1.2.3/internal/mirror",
    )
    out = obs.as_json()
    assert marker not in json.dumps(out)
    assert out["field_names"] == ["status", "<unsafe>"]  # sorted(raw keys), gated
    assert "<redacted:url-userinfo>" in out["detail"]
    assert "10.1.2.3" not in out["detail"]


# ---------------------------------------------------------------------------
# receipts.py: manifest redaction, post-summary screening, chain + seal
# ---------------------------------------------------------------------------

from hunter_local_ingest.receipts import (  # noqa: E402
    CHAIN_FILE,
    SCREEN_FILE,
    Check,
    ChecksWriter,
    Status,
    Transport,
    verify_run_integrity,
    write_run_manifest,
    write_summary,
)


def _manifest_kwargs(**over: object) -> dict:
    kwargs: dict = {
        "endpoint": "http://127.0.0.1:8000/v1",
        "backend_ref": None,
        "backend_mode": "REAL_MIRROR_CPU",
        "client_ref": None,
        "adapter_interface": "iface/1",
        "fixture_identity": "gen_synthetic_packet seed=3",
        "seed": 3,
        "commands": ["hunter-local-ingest run --seed 3"],
        "label": "run",
        "origin_note": "origin",
    }
    kwargs.update(over)
    return kwargs


def _check(check_id: str, observed: str) -> Check:
    return Check(
        id=check_id,
        transport=Transport.REAL_MIRROR_CPU,
        oracle="published contract",
        expected="terminal status",
        observed=observed,
        status=Status.PASS,
    )


def test_manifest_never_records_raw_endpoint(tmp_path):
    write_run_manifest(tmp_path, **_manifest_kwargs(endpoint="http://u:p@127.0.0.1:9443/v1?x=1"))
    text = (tmp_path / "run-manifest.json").read_text()
    assert "u:p" not in text and "9443" in text  # host:port label only


def test_manifest_redacts_non_loopback_endpoint(tmp_path):
    write_run_manifest(tmp_path, **_manifest_kwargs(endpoint="https://mirror.internal.example/v1"))
    manifest = json.loads((tmp_path / "run-manifest.json").read_text())
    assert manifest["endpoint"] == "<redacted:endpoint>"
    assert "mirror.internal.example" not in json.dumps(manifest)


def test_manifest_redacts_commands_and_lookalike_loopback(tmp_path):
    token = canary("key")
    write_run_manifest(
        tmp_path,
        **_manifest_kwargs(
            endpoint="http://127.0.0.1.evil.com:8000/v1",
            commands=[f"hunter-local-ingest run --api-key {token}"],
        ),
    )
    manifest = json.loads((tmp_path / "run-manifest.json").read_text())
    assert manifest["endpoint"] == "<redacted:endpoint>"
    assert token not in json.dumps(manifest)
    assert manifest["commands"][0].endswith("--api-key <redacted:credential-kv>")


def test_clean_run_summary_screens_clean_and_seals(tmp_path):
    write_run_manifest(tmp_path, **_manifest_kwargs())
    writer = ChecksWriter(tmp_path)
    writer.write(_check("lifecycle.health", "http 200; fields=status"))
    writer.close()
    checks_list = [_check("lifecycle.health", "http 200; fields=status")]
    findings = write_summary(tmp_path, checks_list, lead_questions=[], blocked=[])
    assert findings == []
    record = json.loads((tmp_path / SCREEN_FILE).read_text())
    assert record["clean"] is True
    for name in ("run-manifest.json", "checks.jsonl", "summary.json", CHAIN_FILE):
        assert not ((tmp_path / name).stat().st_mode & 0o222), name
    assert verify_run_integrity(tmp_path) == []


def test_write_summary_refuses_rewrite(tmp_path):
    write_summary(tmp_path, [], lead_questions=[], blocked=[])
    with pytest.raises(SystemExit):
        write_summary(tmp_path, [], lead_questions=[], blocked=[])


def test_planted_canary_is_screened_and_evidence_survives(tmp_path):
    """A regression that bypasses write-time redaction is still caught by
    the post-summary screen; the evidence is never deleted or rewritten."""
    marker = canary("mftok")
    write_run_manifest(tmp_path, **_manifest_kwargs())
    # simulate a future unscreened field writer (regression)
    path = tmp_path / "run-manifest.json"
    path.write_text(path.read_text().replace("origin", f"origin Bearer {marker}"))
    checks = [_check("lifecycle.health", "ok")]
    findings = write_summary(tmp_path, checks, lead_questions=[], blocked=[])
    assert any("bearer-token" in f for f in findings)
    record = json.loads((tmp_path / SCREEN_FILE).read_text())
    assert record["clean"] is False and record["findings"]
    assert marker not in json.dumps(record)  # findings never echo material
    assert marker in path.read_text()  # failed evidence preserved, sealed
    assert not (path.stat().st_mode & 0o222)
    assert verify_run_integrity(tmp_path)  # chain pins the pre-plant digest too


def test_verify_detects_tamper_and_missing_chain(tmp_path):
    write_run_manifest(tmp_path, **_manifest_kwargs())
    writer = ChecksWriter(tmp_path)
    writer.write(_check("lifecycle.health", "ok"))
    writer.close()
    write_summary(tmp_path, [_check("lifecycle.health", "ok")], lead_questions=[], blocked=[])
    assert verify_run_integrity(tmp_path) == []

    checks = tmp_path / "checks.jsonl"
    checks.chmod(0o644)
    checks.write_text(checks.read_text() + "tampered line\n")
    problems = verify_run_integrity(tmp_path)
    assert any("checks.jsonl: modified after chain entry" in p for p in problems)

    empty = tmp_path.parent / "empty-run"
    empty.mkdir()
    assert verify_run_integrity(empty) == [f"missing {CHAIN_FILE}"]


def test_failure_output_helper(tmp_path):
    from hunter_local_ingest.sanitize import safe_failure

    token = canary("tok")
    line = safe_failure(
        ["hunter-local-ingest", "run", "--endpoint", f"https://u:{token}@10.1.2.3/v1"],
        f"transport error: Authorization: Bearer {token} refused",
    )
    assert token not in line
    assert "<redacted:url-userinfo>" in line
    assert "<redacted:bearer-token>" in line


# ---------------------------------------------------------------------------
# Operator counterexample controls (CE1-CE4) — failing-first.
#
# These encode the four defects found in static review of joined checkpoint
# 1df1f62c (direction 2). Canary material is synthetic and assembled at
# runtime; nothing is a real credential. No test performs network I/O: the
# lookalike host below is NEVER contacted — redaction/screening unit tests
# only.
#
# CE1  shape-valid server strings (ids, paths, unknown keys) survive
#      observations.py -> fix: published enumerations + run-bound expected
#      values + bounded digests for everything else
# CE2  whitespace-separated credential flags missed by text screening;
#      manifest commands serialized as guessed shell strings -> fix: one
#      structured argv boundary covering every supported flag
# CE3  "127."-prefix hostnames treated as loopback -> fix: strict parsed-IP
#      policy shared by endpoint, command, and failure screening
# CE4  missing mandatory receipts screened as clean / verifier silent
#      -> fix: mandatory-artifact failures independent of chain entries

_CE_RUN_ID = "CANARY-id-012345abcdef"
_CE_PATH = "CANARY-path-012345abcdef"
_CE_TOKEN = "CANARY-token-012345abcdef"
_CE_FIELD = "CANARY-field-012345abcdef"
_CE_LOOKALIKE = "http://127.0.0.1.evil.com:8000/v1"


@pytest.fixture(autouse=True)
def _ce_clean_binding():
    """Isolate the run-bound expected-value registry around every test."""
    from hunter_local_ingest.observations import reset_expected

    reset_expected()
    yield
    reset_expected()


def _ce_observation(body: dict, detail: str | None = None) -> dict:
    from hunter_local_ingest.observations import Observation

    return Observation.from_http(
        200, json.dumps(body).encode("utf-8"), 5, detail
    ).as_json()


def _ce_write_manifest(run_dir, *, commands):
    from hunter_local_ingest.receipts import write_run_manifest

    write_run_manifest(
        run_dir,
        endpoint="http://127.0.0.1:8080/v1",
        backend_ref=None,
        backend_mode="FAKE_HUNTER",
        client_ref=None,
        adapter_interface="CaptureClient",
        fixture_identity="synthetic/seed=1",
        seed=1,
        commands=commands,
        label="ce-control",
        origin_note="test",
    )


# --- CE1: value-level retention under allowed keys -----------------------


def test_ce1_unbound_runid_canary_is_not_retained_verbatim():
    out = _ce_observation({"captureId": "pkt-ce", "runId": _CE_RUN_ID})
    serialized = json.dumps(out)
    assert _CE_RUN_ID not in serialized
    assert out["safe_values"]["runId"].startswith("sha256:")


def test_ce1_unbound_committed_path_canary_is_not_retained():
    out = _ce_observation({"committed": [_CE_PATH, "kf_0001.jpg"]})
    serialized = json.dumps(out)
    assert _CE_PATH not in serialized
    assert out["safe_values"]["committed"][0].startswith("sha256:")


def test_ce1_bound_expected_values_are_retained_verbatim():
    from hunter_local_ingest.observations import bind_expected

    bind_expected([_CE_RUN_ID, _CE_PATH])
    out = _ce_observation({"runId": _CE_RUN_ID, "committed": [_CE_PATH]})
    assert out["safe_values"]["runId"] == _CE_RUN_ID
    assert out["safe_values"]["committed"] == [_CE_PATH]


def test_ce1_published_enumerations_are_retained_raw():
    out = _ce_observation(
        {
            "status": "manual_review",
            "code": "result_not_ready",
            "type": "verdict_ready",
            "next": "retry_finalize",
            "decision": "eligible",
            "schema_version": "0.4",
        }
    )
    safe = out["safe_values"]
    assert safe["status"] == "manual_review"
    assert safe["code"] == "result_not_ready"
    assert safe["type"] == "verdict_ready"
    assert safe["next"] == "retry_finalize"
    assert safe["decision"] == "eligible"
    assert safe["schema_version"] == "0.4"
    # Negative control: a non-published status word reduces to a digest.
    out2 = _ce_observation({"status": "definitely-not-published"})
    assert out2["safe_values"]["status"].startswith("sha256:")


def test_ce1_unknown_field_name_becomes_bounded_digest():
    out = _ce_observation({"captureId": "pkt-ce", _CE_FIELD: "x"})
    serialized = json.dumps(out)
    assert _CE_FIELD not in serialized
    assert any(name.startswith("field:") for name in out["field_names"])


def test_ce1_published_field_names_stay_raw():
    out = _ce_observation({"captureId": "pkt-ce", "viewsNeeded": 2})
    assert "captureId" in out["field_names"]
    assert "viewsNeeded" in out["field_names"]


# --- CE2: credential flags across every argument form ---------------------


def test_ce2_every_credential_flag_covered_in_both_argument_forms():
    from hunter_local_ingest.sanitize import _CREDENTIAL_FLAG_NAMES

    for required in ("token", "secret", "client-secret", "refresh-token"):
        assert required in _CREDENTIAL_FLAG_NAMES
    for flag in sorted(_CREDENTIAL_FLAG_NAMES):
        separated = redact_command(["run", f"--{flag}", _CE_TOKEN])
        equals = redact_command(["run", f"--{flag}={_CE_TOKEN}"])
        text_separated = redact_text(f"run --{flag} {_CE_TOKEN}")
        text_equals = redact_text(f"run --{flag}={_CE_TOKEN}")
        text_quoted = redact_text(f'run --{flag} "{_CE_TOKEN}"')
        for rendered in (
            separated,
            equals,
            text_separated,
            text_equals,
            text_quoted,
        ):
            assert _CE_TOKEN not in rendered, f"--{flag} leaked: {rendered!r}"


def test_ce2_manifest_accepts_structured_argv(tmp_path):
    run_dir = tmp_path / "ce2-run"
    run_dir.mkdir()
    _ce_write_manifest(
        run_dir,
        commands=[["hunter-local-ingest", "run", "--token", _CE_TOKEN]],
    )
    manifest_text = (run_dir / "run-manifest.json").read_text(encoding="utf-8")
    assert _CE_TOKEN not in manifest_text
    assert "***" in manifest_text


def test_ce2_screen_catches_separated_token_inside_an_artifact(tmp_path):
    line = (
        json.dumps(
            {
                "id": "ce2",
                "transport": "FAKE_HUNTER",
                "oracle": "public-contract",
                "expected": "no secret in receipts",
                "observed": f"argv: hunter-local-ingest run --token {_CE_TOKEN}",
                "status": "FAIL",
            }
        )
        + "\n"
    )
    artifact = tmp_path / "checks.jsonl"
    artifact.write_text(line, encoding="utf-8")
    assert screen_artifact(str(artifact)), "separated --token form not caught"


# --- CE3: strict parsed-IP loopback policy --------------------------------


def test_ce3_lookalike_loopback_host_is_not_loopback():
    text = f"endpoint {_CE_LOOKALIKE} refused"
    assert "<redacted:private-endpoint>" in redact_text(text)
    assert "evil.com" not in redact_text(text)
    argv = redact_command(["hunter-local-ingest", "run", "--endpoint", _CE_LOOKALIKE])
    assert "evil.com" not in argv, f"lookalike survived argv redaction: {argv!r}"


def test_ce3_strict_loopback_positives_still_pass():
    for url in (
        "http://127.0.0.1:8080/v1",
        "http://[::1]:8080/v1",
        "http://localhost:8080/v1",
        "http://127.29.0.4:8080/v1",
    ):
        text = f"endpoint {url} ok"
        assert sanitize.findings_in_text(text) == [], url
        assert redact_text(text) == text, url


def test_ce3_lookalike_inside_emitted_command_is_screened(tmp_path):
    artifact = tmp_path / "checks.jsonl"
    artifact.write_text(
        json.dumps({"observed": f"ran with endpoint {_CE_LOOKALIKE}"}) + "\n",
        encoding="utf-8",
    )
    findings = screen_artifact(str(artifact))
    assert findings, "lookalike endpoint not caught by the screen"
    assert "private-endpoint" in findings[0]


def test_ce3_manifest_endpoint_lookalike_is_redacted(tmp_path):
    run_dir = tmp_path / "ce3-run"
    run_dir.mkdir()
    from hunter_local_ingest.receipts import write_run_manifest

    write_run_manifest(
        run_dir,
        endpoint=_CE_LOOKALIKE,
        backend_ref=None,
        backend_mode="FAKE_HUNTER",
        client_ref=None,
        adapter_interface="CaptureClient",
        fixture_identity="synthetic/seed=1",
        seed=1,
        commands=["hunter-local-ingest run"],
        label="ce3",
        origin_note="test",
    )
    manifest_text = (run_dir / "run-manifest.json").read_text(encoding="utf-8")
    assert "evil.com" not in manifest_text
    assert "<redacted:endpoint>" in manifest_text


# --- CE4: mandatory receipts are never silently absent --------------------


def test_ce4_missing_mandatory_artifacts_are_non_green(tmp_path):
    from hunter_local_ingest.receipts import SCREEN_FILE, write_summary

    findings = write_summary(tmp_path, [], lead_questions=[], blocked=[])
    joined = "\n".join(findings)
    assert "run-manifest.json" in joined, joined
    assert "checks.jsonl" in joined, joined
    record = json.loads((tmp_path / SCREEN_FILE).read_text(encoding="utf-8"))
    assert record["clean"] is False


def test_ce4_verifier_names_missing_mandatory_without_chain(tmp_path):
    from hunter_local_ingest.receipts import verify_run_integrity

    problems = verify_run_integrity(tmp_path)
    joined = "\n".join(problems)
    assert "run-manifest.json" in joined, joined
    assert "checks.jsonl" in joined, joined


def test_ce4_verifier_names_missing_mandatory_with_chain(tmp_path):
    from hunter_local_ingest.receipts import verify_run_integrity

    (tmp_path / "privacy-chain.jsonl").write_text("", encoding="utf-8")
    problems = verify_run_integrity(tmp_path)
    joined = "\n".join(problems)
    assert "run-manifest.json" in joined, joined
    assert "checks.jsonl" in joined, joined


def test_ce4_empty_checks_file_is_flagged(tmp_path):
    from hunter_local_ingest.receipts import SCREEN_FILE, write_summary

    _ce_write_manifest(tmp_path, commands=["hunter-local-ingest run"])
    (tmp_path / "checks.jsonl").write_text("", encoding="utf-8")
    findings = write_summary(tmp_path, [], lead_questions=[], blocked=[])
    joined = "\n".join(findings)
    assert "checks.jsonl" in joined, joined
    assert "empty" in joined or "missing" in joined, joined
    record = json.loads((tmp_path / SCREEN_FILE).read_text(encoding="utf-8"))
    assert record["clean"] is False
