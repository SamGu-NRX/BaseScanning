"""Fault-oracle tests: new proxy kinds, their gates, wire behavior, the
real restart control, and the self-check's mutation controls.

These prove the fault proxy actually applies each new fault (truncated
response framing, expired signed-URL answer, canned unknown-capture
answer, retry_finalize event injection) and that the after/recover_after
gates release it. The restart-control tests prove the executed command,
the state-wipe probe, and both published recovery outcomes. The mutation
controls disable a harness detection and prove the round evaluator fails
closed — a fault that fires without a matching detection, a round that
never fired, a missing signature, or a stripped failed event are all
self-check problems, never silent passes.
"""

from __future__ import annotations

import http.client
import json
import threading
import urllib.parse
from http.server import ThreadingHTTPServer

import pytest

from hunter_local_ingest.faultproxy import (
    _inject_retry_finalize,
    _truncate_response_payload,
    serve,
)
from hunter_local_ingest.http_client import DirectHttpClient
from hunter_local_ingest.modes import Status, Transport
from hunter_local_ingest.scenarios.lifecycle import run_lifecycle
from hunter_local_ingest.selfcheck import (
    _FakeState,
    build_fake_handler,
    build_plan,
    fault_round_problems,
    run_restart_control,
    selfcheck_manifest_commands,
)


@pytest.fixture()
def fake_origin():
    """The self-check plumbing fake on a loopback port."""
    holder = {"base": ""}
    state = _FakeState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_fake_handler(holder))
    server.state = state  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    holder["base"] = f"http://{host}:{port}"
    yield holder["base"], state
    server.shutdown()


def _open(url: str) -> http.client.HTTPConnection:
    parsed = urllib.parse.urlparse(url)
    return http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)


def _create_capture(url: str) -> str:
    conn = _open(url)
    conn.request(
        "POST", "/captures", body=b"{}", headers={"Content-Type": "application/json"}
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read())
    conn.close()
    assert resp.status == 201
    return payload["captureId"]


def _register(origin_url: str, cap_id: str, paths: list[str]) -> None:
    body = json.dumps(
        {
            "files": [
                {"path": p, "sha256": "0" * 64, "bytes": 32, "role": "other"}
                for p in paths
            ]
        }
    ).encode("utf-8")
    conn = _open(origin_url)
    conn.request(
        "POST",
        f"/captures/{cap_id}/files",
        body=body,
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 200


# -- truncated response framing ---------------------------------------------


def test_truncate_response_framing_is_surfaced_as_a_read_failure(fake_origin):
    origin, _state = fake_origin
    proxy = serve(origin, {"commit": {"kind": "truncate_response", "bytes": 8}})
    try:
        cap_id = _create_capture(origin)
        conn = _open(proxy.base_url)
        conn.request(
            "POST",
            f"/captures/{cap_id}/files:commit",
            body=json.dumps({"files": []}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        with pytest.raises((http.client.IncompleteRead, ConnectionError, OSError)):
            resp.read()
        conn.close()
        assert proxy.injected_counts.get("commit") == 1
    finally:
        proxy.stop()


def test_truncate_response_payload_declares_more_than_it_writes():
    written, declared = _truncate_response_payload(b"x" * 100, {"bytes": 8})
    assert len(written) == 92
    assert declared == 100

    # an empty upstream payload still produces a framing mismatch
    empty_written, empty_declared = _truncate_response_payload(b"", {"bytes": 8})
    assert empty_written == b""
    assert empty_declared == 8


# -- expired signed-URL answer -----------------------------------------------


def test_expired_url_fault_then_recovery(fake_origin):
    origin, state = fake_origin
    proxy = serve(origin, {"upload": {"kind": "expired_url", "recover_after": 1}})
    try:
        cap_id = _create_capture(origin)
        _register(origin, cap_id, ["synthetic/selfcheck0.syn"])

        conn = _open(proxy.base_url)
        conn.request("PUT", f"/storage/{cap_id}/0", body=b"x" * 32)
        resp = conn.getresponse()
        payload = json.loads(resp.read())
        conn.close()
        assert resp.status == 403
        assert payload["errors"][0]["code"] == "harness_url_expired"

        # recovery: the second PUT passes through to the origin and lands
        conn = _open(proxy.base_url)
        conn.request("PUT", f"/storage/{cap_id}/0", body=b"x" * 32)
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 200
        assert state.storage[f"{cap_id}/synthetic/selfcheck0.syn"] == b"x" * 32
        assert proxy.injected_counts.get("upload") == 1
    finally:
        proxy.stop()


# -- canned unknown-capture answer -------------------------------------------


def test_capture_not_found_canned_answer_uses_the_published_code(fake_origin):
    origin, _state = fake_origin
    proxy = serve(origin, {"commit": {"kind": "capture_not_found"}})
    try:
        conn = _open(proxy.base_url)
        conn.request(
            "POST",
            "/captures/cap-0001/files:commit",
            body=json.dumps({"files": []}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read())
        conn.close()
        assert resp.status == 404
        assert payload["errors"][0]["code"] == "capture_not_found"
        assert proxy.injected_counts.get("commit") == 1
    finally:
        proxy.stop()


def test_capture_not_found_can_target_any_capture_step(fake_origin):
    origin, _state = fake_origin
    faults = {
        "create": {"kind": "capture_not_found"},
        "register": {"kind": "capture_not_found"},
        "finalize": {"kind": "capture_not_found"},
    }
    proxy = serve(origin, faults)
    try:
        conn = _open(proxy.base_url)
        conn.request(
            "POST", "/captures", body=b"{}", headers={"Content-Type": "application/json"}
        )
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 404

        conn = _open(proxy.base_url)
        conn.request(
            "POST",
            "/captures/cap-0001/files",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 404
    finally:
        proxy.stop()


# -- retry_finalize event injection ------------------------------------------


def test_retry_finalize_injection_then_recovery(fake_origin):
    origin, _state = fake_origin
    proxy = serve(origin, {"events": {"kind": "retry_finalize", "recover_after": 1}})
    try:
        cap_id = _create_capture(origin)

        conn = _open(proxy.base_url)
        conn.request("GET", f"/captures/{cap_id}/events")
        resp = conn.getresponse()
        body = json.loads(resp.read())
        conn.close()
        injected = [e for e in body["events"] if e["type"] == "failed"]
        assert len(injected) == 1
        assert injected[0]["data"] == {"next": "retry_finalize"}
        assert body["next"] >= injected[0]["seq"]

        # recovery: the next poll is the clean upstream answer
        conn = _open(proxy.base_url)
        conn.request("GET", f"/captures/{cap_id}/events")
        resp = conn.getresponse()
        clean = json.loads(resp.read())
        conn.close()
        assert all(e["type"] != "failed" for e in clean["events"])
        assert proxy.injected_counts.get("events") == 1
    finally:
        proxy.stop()


def test_inject_retry_finalize_ignores_bodies_that_carry_no_events():
    assert _inject_retry_finalize(b'{"status": "ok"}') is None
    assert _inject_retry_finalize(b"not json at all") is None


# -- gating ------------------------------------------------------------------


def test_after_gate_releases_early_hits(fake_origin):
    origin, _state = fake_origin
    proxy = serve(origin, {"commit": {"kind": "capture_not_found", "after": 2}})
    try:
        cap_id = _create_capture(origin)
        statuses = []
        for _ in range(3):
            conn = _open(proxy.base_url)
            conn.request(
                "POST",
                f"/captures/{cap_id}/files:commit",
                body=json.dumps({"files": []}),
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            resp.read()
            conn.close()
            statuses.append(resp.status)
        assert statuses == [200, 200, 404]
        assert proxy.hit_counts["commit"] == 3
        assert proxy.injected_counts.get("commit") == 1
    finally:
        proxy.stop()


# -- restart control (real --restart-command behavior) ------------------------


def test_restart_control_recovers_after_state_loss(fake_origin):
    """The command executes, the fake state is wiped, the pre-restart
    capture 404s, and the full recovery lifecycle completes under the SAME
    idempotency key."""
    origin, state = fake_origin
    client = DirectHttpClient(origin, timeout_s=10.0)
    status, observed = run_restart_control(
        client, build_plan(), restart_command="true", reset_state=state.hard_reset
    )
    assert status is Status.PASS
    assert "restart command exit=0" in observed
    assert "simulated process memory loss" in observed
    assert "404 post-restart" in observed
    assert "recovery lifecycle complete" in observed


def test_restart_control_passes_when_state_is_preserved(fake_origin):
    """Without a wipe the restarted backend still answers for the earlier
    capture, and the replayed idempotency key returns the same capture."""
    origin, _state = fake_origin
    client = DirectHttpClient(origin, timeout_s=10.0)
    status, observed = run_restart_control(client, build_plan(), restart_command="true")
    assert status is Status.PASS
    assert "2xx post-restart (state preserved)" in observed
    assert "recovery lifecycle complete" in observed


def test_restart_control_blocks_when_the_command_fails(fake_origin):
    origin, _state = fake_origin
    client = DirectHttpClient(origin, timeout_s=10.0)
    status, observed = run_restart_control(client, build_plan(), restart_command="exit 7")
    assert status is Status.BLOCKED
    assert "restart command exit=7" in observed
    assert "recovery not attempted" in observed


def test_manifest_commands_fingerprint_the_restart_command():
    """The command text never reaches the manifest — a credential the
    pattern redactor cannot see (bare -u user:pass) must still not leak."""
    command = "curl -u user:secret-token-xyz http://127.0.0.1/restart"
    commands = selfcheck_manifest_commands(command)
    assert commands[0] == "hunter-selfcheck"
    assert len(commands) == 2
    assert commands[1].startswith("--restart-command=<fingerprint:sha256-")
    assert "secret-token-xyz" not in commands[1]
    assert commands[1] == selfcheck_manifest_commands(command)[1]
    assert selfcheck_manifest_commands(None) == ["hunter-selfcheck"]


# -- mutation controls (anti-blind-spot evidence) -----------------------------


_TRUNC_SPEC = {
    "prefix": "trunc.",
    "faults": {"commit": {"kind": "truncate_response", "bytes": 8}},
    "step": "commit",
    "signature": "IncompleteRead",
    "expect_not_pass": ("lifecycle.commit",),
}


def _faulted_commit_round_sink() -> list[dict]:
    """A sink as the live trunc. round emits it: fault fired and detected."""
    return [
        {
            "id": "trunc.lifecycle.commit",
            "transport": Transport.FAKE_HUNTER.value,
            "oracle": "oracle",
            "expected": "non-pass with the read failure named",
            "observed": "transport after 3 attempt(s): IncompleteRead(",
            "status": Status.BLOCKED.value,
        }
    ]


def test_mutation_control_passes_when_the_round_is_honest():
    sink = _faulted_commit_round_sink()
    assert fault_round_problems(sink, {"commit": 3}, _TRUNC_SPEC) == []


def test_mutation_vacuous_pass_is_flagged():
    """Disabling the detection — the faulted step records PASS — must fail
    the round evaluation instead of reading as success."""
    sink = _faulted_commit_round_sink()
    sink[0]["status"] = Status.PASS.value
    problems = fault_round_problems(sink, {"commit": 3}, _TRUNC_SPEC)
    assert any("expected a non-pass row" in p for p in problems)


def test_mutation_fault_never_fired_is_flagged():
    problems = fault_round_problems(_faulted_commit_round_sink(), {}, _TRUNC_SPEC)
    assert any("fault never fired" in p for p in problems)


def test_mutation_wrong_signature_is_flagged():
    sink = _faulted_commit_round_sink()
    sink[0]["observed"] = "transport after 3 attempt(s): ConnectionReset("
    problems = fault_round_problems(sink, {"commit": 3}, _TRUNC_SPEC)
    assert any("expected a non-pass row" in p for p in problems)


def test_mutation_missing_retry_finalize_event_is_flagged(fake_origin):
    """Stripping the injected failed event from a REAL observation must fail
    the round evaluation."""
    origin, _state = fake_origin
    proxy = serve(origin, {"events": {"kind": "retry_finalize", "recover_after": 1}})
    sink: list[dict] = []
    try:
        client = DirectHttpClient(proxy.base_url, timeout_s=10.0)
        run_lifecycle(
            client,
            build_plan(),
            Transport.FAKE_HUNTER,
            sink,
            result_budget_s=15.0,
            poll_interval_s=0.2,
        )
    finally:
        proxy.stop()
    spec = {"prefix": "", "step": "events", "require_event_type": "failed"}
    assert fault_round_problems(sink, dict(proxy.injected_counts), spec) == []

    row = next(line for line in sink if line["id"] == "lifecycle.events")
    events = row["observation"].safe_values["events"]
    row["observation"].safe_values["events"] = [
        e for e in events if e.get("type") != "failed"
    ]
    problems = fault_round_problems(sink, dict(proxy.injected_counts), spec)
    assert any("failed" in p and "event" in p for p in problems)
