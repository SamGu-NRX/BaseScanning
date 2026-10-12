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
