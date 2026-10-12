"""Lifecycle and controls against the fake API, with independent oracles.

The success oracle here does not trust the harness's own verdict: it reads
the fake's memory and checks that the baseline capture holds exactly the
plan's bytes under exactly the plan's paths, that each control used a
capture of its own, and that the run and status the receipt names are
the ones the server minted. The misbehaving-fake cases then show each
check fails when it should.
"""

from __future__ import annotations

import hashlib

import pytest

from hunter_local_ingest.fakeapi import FakeConfig
from hunter_local_ingest.http_client import DirectHttpClient
from hunter_local_ingest.modes import Status, Transport
from hunter_local_ingest.scenarios import LIFECYCLE_IDS
from hunter_local_ingest.scenarios.controls import run_controls
from hunter_local_ingest.scenarios.lifecycle import run_lifecycle


def _client(api, **kw) -> DirectHttpClient:
    return DirectHttpClient(api.base_url, retry_backoff_s=0.0, **kw)


def _run(api, plan, budget=5.0, client=None):
    sink: list = []
    outcome = run_lifecycle(
        client or _client(api),
        plan,
        Transport.FAKE_HUNTER,
        sink,
        result_budget_s=budget,
        poll_interval_s=0.01,
        events_wait_s=0,
    )
    return {line["id"]: line for line in sink}, outcome


def test_success_is_checked_against_the_servers_memory(fake, plan):
    with fake() as api:
        rows, outcome = _run(api, plan)
        state = api.state
        assert all(rows[i]["status"] == "PASS" for i in LIFECYCLE_IDS), rows
        cap = state.captures[state.by_packet[plan.packet_id]]
        # exactly the plan's paths, each holding exactly the plan's bytes
        assert set(cap["stored"]) == plan.declared_paths
        for f in plan.files:
            assert hashlib.sha256(cap["stored"][f.path]).hexdigest() == f.sha256
        assert cap["committed"] == plan.declared_paths
        # the receipt's identities are the ones this server minted
        assert outcome.capture_id == cap["id"] and outcome.run_id == cap["run"]
        assert outcome.terminal_status == "complete"
        result = rows["lifecycle.result"]["observed"]
        assert result["run_matches"] and result["status_matches"]


@pytest.mark.parametrize(
    ("answer", "terminal"),
    [
        ("candidate", "complete"),
        ("not_eligible", "complete"),
        ("needs_views", "needs_views"),
        ("manual_review", "manual_review"),
        ("failed", "failed"),
        ("expired", "expired"),
    ],
)
def test_every_published_terminal_status_ends_the_lifecycle(fake, plan, answer, terminal):
    with fake(FakeConfig(answer=answer)) as api:
        rows, outcome = _run(api, plan)
    assert rows["lifecycle.events"]["status"] == "PASS"
    assert rows["lifecycle.result"]["status"] == "PASS", rows["lifecycle.result"]
    assert outcome.terminal_status == terminal


def test_result_for_another_run_fails(fake, plan):
    with fake(FakeConfig(answer="wrong_run")) as api:
        rows, _ = _run(api, plan)
    assert rows["lifecycle.result"]["status"] == "FAIL"
    assert rows["lifecycle.result"]["observed"]["note"] == "result-runid-mismatch"


def test_result_never_ready_fails_after_bounded_reads(fake, plan):
    with fake(FakeConfig(answer="not_ready")) as api:
        rows, _ = _run(api, plan)
    observed = rows["lifecycle.result"]["observed"]
    assert rows["lifecycle.result"]["status"] == "FAIL"
    assert observed["note"] == "not-ready-limit" and observed["not_ready_reads"] == 11


def test_a_capture_that_never_ends_is_blocked_not_missing(fake, plan):
    with fake(FakeConfig(answer="hold")) as api:
        rows, _ = _run(api, plan, budget=0.3)
    assert rows["lifecycle.events"]["status"] == "BLOCKED"
    assert rows["lifecycle.events"]["observed"]["note"] == "budget-exhausted"
    assert rows["lifecycle.result"]["status"] == "BLOCKED"
    assert rows["lifecycle.result"]["observed"]["note"] == "skipped-after-blocked-step"


def test_retry_finalize_resends_the_packet(fake, plan):
    with fake(FakeConfig(retry_finalize_once=True)) as api:
        rows, outcome = _run(api, plan)
        cap = api.state.captures[api.state.by_packet[plan.packet_id]]
    assert rows["lifecycle.events"]["observed"]["finalize_retries"] == 1
    assert rows["lifecycle.result"]["status"] == "PASS"
    assert outcome.run_id == cap["run"]


def test_reordered_register_answer_still_uploads_each_file_to_its_own_target(fake, plan):
    with fake(FakeConfig(register_order="reversed")) as api:
        rows, _ = _run(api, plan)
        cap = api.state.captures[api.state.by_packet[plan.packet_id]]
    # a response-order mapping would put bytes under the wrong path and the
    # digest-checking commit would report mismatches
    assert rows["lifecycle.commit"]["status"] == "PASS"
    for f in plan.files:
        assert hashlib.sha256(cap["stored"][f.path]).hexdigest() == f.sha256


@pytest.mark.parametrize(
    ("config", "violation"),
    [
        (FakeConfig(register_drop_last=True), "not-acknowledged:files"),
        (FakeConfig(register_duplicate_first=True), "duplicate:files"),
        (FakeConfig(register_extra_path=True), "unexpected-path:files"),
        (FakeConfig(storage_base="http://storage.invalid"), "upload_url_insecure"),
    ],
)
def test_register_misbehaviour_fails_and_later_steps_are_blocked(fake, plan, config, violation):
    with fake(config) as api:
        rows, _ = _run(api, plan)
        stored = sum(len(c["stored"]) for c in api.state.captures.values())
    assert rows["lifecycle.register"]["status"] == "FAIL"
    assert violation in rows["lifecycle.register"]["observed"]["violations"]
    for later in ("lifecycle.upload", "lifecycle.commit", "lifecycle.result"):
        assert rows[later]["status"] == "BLOCKED"
        assert rows[later]["observed"]["note"] == "skipped-after-failed-step"
    assert stored == 0, "nothing is uploaded after a bad register answer"


def test_files_already_committed_at_register_are_not_uploaded(fake, plan):
    with fake(FakeConfig(register_all_committed=True)) as api:
        rows, _ = _run(api, plan)
        puts = len(api.state.put_headers)
    assert rows["lifecycle.register"]["status"] == "PASS"
    assert rows["lifecycle.upload"]["observed"]["uploads"] == []
    assert puts == 0


def test_signed_put_carries_exactly_the_signed_headers(fake, plan):
    token = "synthetic-api-token"
    with fake(FakeConfig(require_token=token)) as api:
        rows, _ = _run(api, plan, client=_client(api, api_token=token))
        puts = api.state.put_headers
        api_auth = api.state.api_auth
    assert rows["lifecycle.result"]["status"] == "PASS"
    # exactly the signed header plus what HTTP framing needs; no credential
    assert puts and all(set(h) == {"host", "content-length", "content-type"} for h in puts)
    types = {f.content_type for f in plan.files}
    assert {h["content-type"] for h in puts} <= types
    assert set(api_auth) == {f"Bearer {token}"}


def test_unsigned_content_type_is_never_added(fake, plan):
    with fake(FakeConfig(sign_content_type=False)) as api:
        rows, _ = _run(api, plan)
        puts = api.state.put_headers
    assert rows["lifecycle.upload"]["status"] == "PASS"
    assert puts and all(set(h) == {"host", "content-length"} for h in puts)


def test_signed_authorization_is_refused_before_sending(fake, plan):
    with fake(FakeConfig(sign_authorization=True)) as api:
        rows, _ = _run(api, plan, client=_client(api, api_token="api-only-token"))
        puts = len(api.state.put_headers)
    assert rows["lifecycle.upload"]["status"] == "FAIL"
    assert (
        rows["lifecycle.upload"]["observed"]["uploads"][0]["error"] == "auth_storage_authorization"
    )
    assert puts == 0


def test_malformed_api_token_is_refused():
    from hunter_local_ingest.netpolicy import EndpointError

    with pytest.raises(EndpointError):
        DirectHttpClient("http://127.0.0.1:9/v1", api_token="has space")


# -- controls -----------------------------------------------------------------


def _controls(api, plan, client=None):
    sink: list = []
    run_controls(client or _client(api), plan, Transport.FAKE_HUNTER, sink)
    return {line["id"]: line for line in sink}


def test_controls_pass_and_each_uses_its_own_capture(fake, plan):
    with fake() as api:
        _run(api, plan)
        rows = _controls(api, plan)
        by_packet = dict(api.state.by_packet)
        baseline = api.state.captures[by_packet[plan.packet_id]]
    for check_id in (
        "control.idempotent-create",
        "control.corrupt-upload",
        "control.truncated-upload",
        "control.wrong-capture",
    ):
        assert rows[check_id]["status"] == "PASS", rows[check_id]
    expected = {
        plan.packet_id,
        *(f"{plan.packet_id}-{s}" for s in ("idem", "corrupt", "truncated")),
    }
    assert set(by_packet) == expected
    assert len(set(by_packet.values())) == len(expected)
    # tampering never touched the baseline capture's bytes
    for f in plan.files:
        assert hashlib.sha256(baseline["stored"][f.path]).hexdigest() == f.sha256


def test_integrity_control_fails_when_the_server_commits_tampered_bytes(fake, plan):
    with fake(FakeConfig(commit_everything=True)) as api:
        rows = _controls(api, plan)
    assert rows["control.corrupt-upload"]["status"] == "FAIL"
    assert rows["control.corrupt-upload"]["observed"]["tampered_committed"] is True
    assert rows["control.truncated-upload"]["status"] == "FAIL"


def test_integrity_control_passes_when_storage_refuses_the_put(fake, plan):
    with fake(FakeConfig(verify_put_digest=True)) as api:
        rows = _controls(api, plan)
    observed = rows["control.corrupt-upload"]["observed"]
    assert rows["control.corrupt-upload"]["status"] == "PASS"
    assert observed["tampered_put_status"] == 400 and observed["intact_committed"] is True


def test_integrity_control_fails_when_nothing_is_committed(fake, plan, monkeypatch):
    """A server that commits nothing must not pass the tamper controls."""
    with fake() as api:

        def nothing(self, cap, body):
            self._send(200, {"committed": [], "notFound": [], "mismatch": []})

        handler = api._server.RequestHandlerClass
        monkeypatch.setattr(handler, "_commit", nothing)
        rows = _controls(api, plan)
    assert rows["control.corrupt-upload"]["status"] == "FAIL"
    assert rows["control.corrupt-upload"]["observed"]["intact_committed"] is False


def test_idempotent_create_fails_when_a_second_capture_is_minted(fake, plan, monkeypatch):
    with fake() as api:
        original = api._server.RequestHandlerClass._create

        def mint_again(self, body):
            api.state.by_packet.clear()
            original(self, body)

        monkeypatch.setattr(api._server.RequestHandlerClass, "_create", mint_again)
        rows = _controls(api, plan)
    assert rows["control.idempotent-create"]["status"] == "FAIL"
    assert rows["control.idempotent-create"]["observed"]["same_capture"] is False


def test_unreachable_endpoint_blocks_every_step(plan):
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    sink: list = []
    run_lifecycle(
        DirectHttpClient(f"http://127.0.0.1:{port}/v1", max_retries=0),
        plan,
        Transport.REAL_API,
        sink,
        result_budget_s=1,
    )
    rows = {line["id"]: line for line in sink}
    assert [rows[i]["status"] for i in LIFECYCLE_IDS] == [Status.BLOCKED.value] * 8
    assert rows["lifecycle.health"]["observed"]["note"] == "endpoint-unreachable"


# -- every final API URL passes the scope gate before any header or request ------

HOSTILE_IDS = ["../outside", "..", ".", "a/b", "a\\b", "x/../../outside"]


@pytest.mark.parametrize("capture_id", HOSTILE_IDS)
def test_hostile_capture_ids_are_refused_before_any_request(fake, plan, capture_id):
    token = "synthetic-api-token"
    with fake() as api:
        client = _client(api, api_token=token)
        f = plan.files[0]
        calls = [
            lambda: client.register(capture_id, [f.register_entry()]),
            lambda: client.commit(capture_id, [f.commit_entry()]),
            lambda: client.finalize(capture_id, plan.packet, plan.packet_id),
            lambda: client.events(capture_id, 0, 0),
            lambda: client.result(capture_id),
        ]
        for call in calls:
            res = call()
            assert res.error == "auth_destination_refused", res
            assert res.attempts == 0 and res.observation is None
        assert api.state.requests == [], "nothing reached the transport"


def test_a_server_minted_traversal_id_never_carries_the_credential(fake, plan):
    token = "synthetic-api-token"
    with fake(FakeConfig(capture_id_override="../outside")) as api:
        rows, _ = _run(api, plan, client=_client(api, api_token=token))
        requests = list(api.state.requests)
    assert rows["lifecycle.create"]["status"] == "PASS"
    assert rows["lifecycle.register"]["status"] == "FAIL"
    assert rows["lifecycle.register"]["observed"]["batches"][0]["http"]["error"] == (
        "auth_destination_refused"
    )
    # only health and create were sent; nothing went to a traversal path
    assert [(m, p.split("?")[0]) for m, p, _auth in requests] == [
        ("GET", "/v1/healthz"),
        ("POST", "/v1/captures"),
    ]
    assert not any("outside" in p or "%2F" in p.upper() for _m, p, _a in requests)


# -- the advertised batch limit holds for controls as well as the lifecycle -------


def test_a_two_file_batch_limit_is_honoured_everywhere(fake, plan):
    assert len(plan.files) == 5
    with fake(FakeConfig(max_batch=2)) as api:
        rows, _ = _run(api, plan)
        controls = _controls(api, plan)
        state = api.state
        by_packet = dict(state.by_packet)
        baseline = state.captures[by_packet[plan.packet_id]]
    assert all(rows[i]["status"] == "PASS" for i in LIFECYCLE_IDS), rows
    for check_id in ("control.corrupt-upload", "control.truncated-upload"):
        observed = controls[check_id]["observed"]
        assert controls[check_id]["status"] == "PASS", controls[check_id]
        assert observed["tampered_reported"] is True and observed["intact_committed"] is True
        assert observed["tampered_committed"] is False
    # the fake answers 413 to any register or commit over two files, so the
    # all-PASS rows above mean every request respected the limit
    assert len(rows["lifecycle.register"]["observed"]["batches"]) == 3
    # each control on a capture of its own; the baseline bytes are untouched
    assert len(set(by_packet.values())) == len(by_packet)
    for f in plan.files:
        assert hashlib.sha256(baseline["stored"][f.path]).hexdigest() == f.sha256


def test_batched_controls_still_fail_when_tampered_bytes_are_committed(fake, plan):
    with fake(FakeConfig(max_batch=2, commit_everything=True)) as api:
        controls = _controls(api, plan)
    for check_id in ("control.corrupt-upload", "control.truncated-upload"):
        assert controls[check_id]["status"] == "FAIL"
        assert controls[check_id]["observed"]["tampered_committed"] is True


# -- files already committed at register: legitimate skips, explicit sets -------


def test_rerun_of_a_fully_committed_capture_skips_put_and_commit(fake, plan):
    with fake() as api:
        _run(api, plan)
        before = len(api.state.requests)
        rows, _ = _run(api, plan)
        second = api.state.requests[before:]
    assert all(rows[i]["status"] == "PASS" for i in LIFECYCLE_IDS), rows
    assert not any(m == "PUT" or p.endswith("files:commit") for m, p, _a in second)
    declared = sorted(plan.declared_paths)
    assert rows["lifecycle.upload"]["observed"]["uploads"] == []
    assert sorted(rows["lifecycle.upload"]["observed"]["committed_at_register"]) == declared
    assert sorted(rows["lifecycle.commit"]["observed"]["committed_at_register"]) == declared
    assert rows["lifecycle.commit"]["observed"]["batches"] == []


def test_partial_replay_uploads_and_commits_only_the_rest(fake, plan):
    with fake() as api:
        client = _client(api)
        res = client.create(plan.create_body, plan.packet_id)
        capture_id = res.parsed["captureId"]
        first = plan.files[:2]
        reg = client.register(capture_id, [f.register_entry() for f in first])
        for entry, f in zip(reg.parsed["files"], first, strict=True):
            assert entry["path"] == f.path
            client.upload(entry["upload"]["url"], f.content, entry["upload"]["headers"])
        client.commit(capture_id, [f.commit_entry() for f in first])
        rows, _ = _run(api, plan)
        cap = api.state.captures[capture_id]
    assert all(rows[i]["status"] == "PASS" for i in LIFECYCLE_IDS), rows
    uploaded = {u["path"] for u in rows["lifecycle.upload"]["observed"]["uploads"]}
    assert uploaded == {f.path for f in plan.files[2:]}
    assert sorted(rows["lifecycle.upload"]["observed"]["committed_at_register"]) == sorted(
        f.path for f in first
    )
    assert cap["committed"] == plan.declared_paths


def test_a_client_that_skips_a_required_put_fails(fake, plan, monkeypatch):
    from hunter_local_ingest.adapter import StepResult

    with fake() as api:
        client = _client(api)
        monkeypatch.setattr(client, "upload", lambda url, body, headers: StepResult("upload"))
        rows, _ = _run(api, plan, client=client)
        puts = [r for r in api.state.requests if r[0] == "PUT"]
    assert puts == []
    assert rows["lifecycle.upload"]["status"] == "FAIL"
    assert rows["lifecycle.commit"]["status"] == "BLOCKED"


def test_result_with_a_malformed_placement_fails_the_lifecycle(fake, plan):
    with fake(FakeConfig(outcome_extra={"recommendedPlacement": 7})) as api:
        rows, _ = _run(api, plan)
    assert rows["lifecycle.result"]["status"] == "FAIL"


# -- contradictory commit acks never pass a tamper control -----------------------


def test_an_intact_path_also_listed_as_mismatch_fails_the_tamper_control(fake, plan):
    intact = plan.files[1].path  # in the victim's batch when maxBatch is 2
    with fake(FakeConfig(max_batch=2, commit_contradict_path=intact)) as api:
        controls = _controls(api, plan)
        rows, _ = _run(api, plan)
    for check_id in ("control.corrupt-upload", "control.truncated-upload"):
        assert controls[check_id]["status"] == "FAIL", controls[check_id]
        assert "overlap:commit-categories" in controls[check_id]["observed"]["violations"]
    assert rows["lifecycle.commit"]["status"] == "FAIL"


def test_the_tamper_oracle_rejects_intact_paths_in_negative_categories(monkeypatch, fake, plan):
    """Even if a decoder let the overlap through, the oracle itself refuses it."""
    from hunter_local_ingest import contract

    original = contract.decode_commit

    def lenient(parsed, requested, declared):
        dec = original(parsed, requested, declared)
        kept = tuple(v for v in dec.violations if v != "overlap:commit-categories")
        return contract.Decoded(kept, dec.public, dec.data)

    from hunter_local_ingest.scenarios import steps

    monkeypatch.setattr(steps, "decode_commit", lenient)
    intact = plan.files[1].path
    with fake(FakeConfig(max_batch=2, commit_contradict_path=intact)) as api:
        controls = _controls(api, plan)
    assert controls["control.corrupt-upload"]["status"] == "FAIL"
    assert controls["control.corrupt-upload"]["observed"]["intact_reported_negative"] is True


# -- a confirmed FAIL in one batch is not hidden by a later unavailable batch ------


def test_register_invalid_first_batch_then_unavailable_stays_fail(fake, plan):
    config = FakeConfig(max_batch=2, register_duplicate_first=True, abort_from={"files": 2})
    with fake(config) as api:
        rows, _ = _run(api, plan, client=_client(api, max_retries=0))
        registers = [r for r in api.state.requests if r[1].endswith("/files")]
    assert rows["lifecycle.register"]["status"] == "FAIL"
    assert "duplicate:files" in rows["lifecycle.register"]["observed"]["violations"]
    # one answered batch is on record; no batch is reported that never answered
    assert len(rows["lifecycle.register"]["observed"]["batches"]) == len(registers) == 1


def test_commit_invalid_first_batch_then_unavailable_stays_fail(fake, plan):
    config = FakeConfig(max_batch=2, commit_extra_path=True, abort_from={"files:commit": 2})
    with fake(config) as api:  # abort counts are per server: one each
        rows, _ = _run(api, plan, client=_client(api, max_retries=0))
    with fake(config) as api:
        controls = _controls(api, plan, client=_client(api, max_retries=0))
    assert rows["lifecycle.commit"]["status"] == "FAIL"
    assert "unexpected-path:committed" in rows["lifecycle.commit"]["observed"]["violations"]
    assert controls["control.corrupt-upload"]["status"] == "FAIL"


def test_a_purely_unavailable_batch_is_still_blocked(fake, plan):
    with fake(FakeConfig(max_batch=2, abort_from={"files": 2})) as api:
        rows, _ = _run(api, plan, client=_client(api, max_retries=0))
    assert rows["lifecycle.register"]["status"] == "BLOCKED"
    batches = rows["lifecycle.register"]["observed"]["batches"]
    assert batches[0]["violations"] == [] and batches[1]["http"]["error"].startswith("transport:")


# -- a request the client refuses, or a malformed answer, is FAIL in the controls ---


def test_control_with_a_traversal_capture_id_fails_without_sending(fake, plan):
    with fake(FakeConfig(capture_id_override="../outside")) as api:
        controls = _controls(api, plan, client=_client(api, api_token="synthetic-token"))
        requests = list(api.state.requests)
    for check_id in ("control.corrupt-upload", "control.truncated-upload"):
        assert controls[check_id]["status"] == "FAIL", controls[check_id]
    assert not any("outside" in p or "%2F" in p.upper() for _m, p, _a in requests)


def test_control_with_a_signed_authorization_fails_without_putting(fake, plan):
    with fake(FakeConfig(sign_authorization=True)) as api:
        controls = _controls(api, plan, client=_client(api, api_token="synthetic-token"))
        puts = [r for r in api.state.requests if r[0] == "PUT"]
    assert controls["control.corrupt-upload"]["status"] == "FAIL"
    assert controls["control.corrupt-upload"]["observed"]["http"]["error"] == (
        "auth_storage_authorization"
    )
    assert puts == []


def test_control_with_a_malformed_register_answer_fails(fake, plan):
    with fake(FakeConfig(register_drop_last=True)) as api:
        controls = _controls(api, plan)
    assert controls["control.corrupt-upload"]["status"] == "FAIL"
    assert "not-acknowledged:files" in controls["control.corrupt-upload"]["observed"]["violations"]


def test_control_with_an_unavailable_register_is_blocked(fake, plan):
    with fake(FakeConfig(abort_from={"files": 1})) as api:
        controls = _controls(api, plan, client=_client(api, max_retries=0))
    assert controls["control.corrupt-upload"]["status"] == "BLOCKED"


# -- a batch whose answer is wrong for THIS scenario decides the round at once ----
#
# Each answer below is decoder-valid. Whether it is acceptable depends on the
# scenario: the lifecycle needs every requested path committed; a tamper
# control needs the victim kept out and every intact file committed.


def _commit_requests(api) -> int:
    return sum(1 for _m, p, _a in api.state.requests if p.endswith("files:commit"))


def test_lifecycle_not_found_batch_then_unavailable_is_fail(fake, plan):
    config = FakeConfig(
        max_batch=2, commit_not_found_path=plan.files[1].path, abort_from={"files:commit": 2}
    )
    with fake(config) as api:
        rows, _ = _run(api, plan, client=_client(api, max_retries=0))
        sent = _commit_requests(api)
    assert rows["lifecycle.commit"]["status"] == "FAIL"
    assert sent == 1, "the round stops at the batch that decided it"
    assert len(rows["lifecycle.commit"]["observed"]["batches"]) == 1


def test_control_victim_committed_then_unavailable_is_fail(fake, plan):
    config = FakeConfig(max_batch=2, commit_everything=True, abort_from={"files:commit": 2})
    with fake(config) as api:
        controls = _controls(api, plan, client=_client(api, max_retries=0))
    # corrupt: its first batch acknowledged the victim, so FAIL, one batch sent
    corrupt = controls["control.corrupt-upload"]
    assert corrupt["status"] == "FAIL", corrupt
    assert len(corrupt["observed"]["batches"]) == 1
    # truncated runs next on the same server; its first commit is the aborted
    # second request, a purely missing answer, so it stays BLOCKED
    assert controls["control.truncated-upload"]["status"] == "BLOCKED"


@pytest.mark.parametrize("knob", ["commit_not_found_path", "commit_omit_path"])
def test_control_intact_file_refused_or_missing_then_unavailable_is_fail(fake, plan, knob):
    intact = plan.files[1].path  # shares the victim's first two-file batch
    config = FakeConfig(max_batch=2, abort_from={"files:commit": 2}, **{knob: intact})
    with fake(config) as api:
        controls = _controls(api, plan, client=_client(api, max_retries=0))
    assert controls["control.corrupt-upload"]["status"] == "FAIL", controls[
        "control.corrupt-upload"
    ]


def test_purely_unavailable_commit_stays_blocked(fake, plan):
    with fake(FakeConfig(max_batch=2, abort_from={"files:commit": 1})) as api:
        rows, _ = _run(api, plan, client=_client(api, max_retries=0))
    with fake(FakeConfig(max_batch=2, abort_from={"files:commit": 1})) as api:
        controls = _controls(api, plan, client=_client(api, max_retries=0))
    assert rows["lifecycle.commit"]["status"] == "BLOCKED"
    assert controls["control.corrupt-upload"]["status"] == "BLOCKED"


def test_expected_tamper_still_passes_across_batches(fake, plan):
    with fake(FakeConfig(max_batch=2)) as api:
        controls = _controls(api, plan)
    for check_id in ("control.corrupt-upload", "control.truncated-upload"):
        assert controls[check_id]["status"] == "PASS"
        assert len(controls[check_id]["observed"]["batches"]) == 3
