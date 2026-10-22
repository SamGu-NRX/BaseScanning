"""Finalize input that the schema alone let through, found by the Z10 audit probes."""

from __future__ import annotations

import json
from asyncio import CancelledError
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event, current_thread

import pytest

from server.api.deps import drain_finalize
from server.api.errors import ApiFailure
from server.api.finalize import accept_packet
from server.api.tests.conftest import photo_packet, sha256_hex
from server.api.tests.test_ingest import _create, _put_commit

BLOB = b"jpeg"


def _ready(client, packet_id: str) -> tuple[str, dict]:
    capture_id = _create(client, packet_id)
    _put_commit(client, capture_id, "stills/s1.jpg", BLOB, meta={"still": "s1"})
    return capture_id, photo_packet(packet_id, [("stills/s1.jpg", BLOB)])


def _post(client, capture_id: str, packet_id: str, raw: bytes):
    return client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={"Idempotency-Key": packet_id, "Content-Type": "application/json"},
    )


def _with_gravity(packet: dict, middle: str) -> bytes:
    packet["stills"][0]["gravity"] = [0.0, 0.25, 0.0]
    return json.dumps(packet).replace("0.25", middle, 1).encode()


def _with_uptime(packet: dict, value: str) -> bytes:
    packet["session"]["startedAtUptime"] = 0.25
    return json.dumps(packet).replace("0.25", value, 1).encode()


# Python's json module reads NaN, Infinity and -Infinity, and turns an
# overflowing literal such as 1e999 into inf. None of these is JSON (RFC 8259),
# and before the fix each of these packets was accepted with 202 and went on to
# processing, because only poses and intrinsics are re-checked in the background.
@pytest.mark.parametrize(
    ("build", "pointer"),
    [
        (lambda pk: _with_gravity(pk, "NaN"), "/stills/0/gravity/1"),
        (lambda pk: _with_gravity(pk, "-Infinity"), "/stills/0/gravity/1"),
        (lambda pk: _with_gravity(pk, "1e999"), "/stills/0/gravity/1"),
        (lambda pk: _with_uptime(pk, "NaN"), "/session/startedAtUptime"),
    ],
    ids=["gravity-nan", "gravity-neg-infinity", "gravity-overflow", "uptime-nan"],
)
def test_non_finite_numbers_are_refused_before_acceptance(client, build, pointer):
    packet_id = "packet-nonfinite"
    capture_id, packet = _ready(client, packet_id)

    response = _post(client, capture_id, packet_id, build(packet))

    assert response.status_code == 422, response.text
    errors = response.json()["errors"]
    assert errors[0]["code"] == "schema"
    assert errors[0]["pointer"] == pointer
    record = client.app_state.state.get_capture(capture_id)
    assert record.packet_sha256 is None
    assert record.status == "uploading"
    # Nothing was accepted, so the corrected packet still finalizes.
    fixed = _post(client, capture_id, packet_id, json.dumps(packet).encode())
    assert fixed.status_code == 202, fixed.text
    assert fixed.json()["status"] == "processing"


def test_a_second_container_cannot_accept_a_different_packet(client):
    """Two API containers (modal_entry.py allows up to four) each load the capture
    before either accepts. The per-capture lock is in-process, so both reach
    accept_packet holding a record with no packet_sha256. Before the fix both
    returned 202: the first client was told about a run that never started, and
    the second body silently replaced the stored packet.json."""
    packet_id = "packet-two-containers"
    capture_id, packet = _ready(client, packet_id)
    deps = client.app_state
    first_view = deps.state.get_capture(capture_id)
    second_view = first_view.model_copy(deep=True)
    first = json.dumps(packet).encode()
    packet["createdAt"] = "2026-09-27T00:00:00.000Z"
    second = json.dumps(packet).encode()

    accepted = accept_packet(deps, first_view, first)
    with pytest.raises(ApiFailure) as refused:
        accept_packet(deps, second_view, second)
    drain_finalize(deps)

    assert accepted["status"] == "processing"
    assert refused.value.status == 409
    assert refused.value.errors[0]["code"] == "packet_sha_conflict"
    assert deps.storage.get_bytes(f"captures/{capture_id}/packet.json") == first
    record = deps.state.get_capture(capture_id)
    assert record.packet_sha256 == sha256_hex(first)
    assert record.run_id == accepted["runId"]


def test_a_second_container_with_the_same_packet_still_replays(client):
    """The claim must not turn an identical retry on another container into a conflict."""
    packet_id = "packet-same-twice"
    capture_id, packet = _ready(client, packet_id)
    deps = client.app_state
    first_view = deps.state.get_capture(capture_id)
    second_view = first_view.model_copy(deep=True)
    raw = json.dumps(packet).encode()

    first = accept_packet(deps, first_view, raw)
    second = accept_packet(deps, second_view, raw)
    drain_finalize(deps)

    assert first["status"] == second["status"] == "processing"
    assert first["runId"] == second["runId"]
    assert deps.state.get_capture(capture_id).packet_sha256 == sha256_hex(raw)


def test_a_failed_packet_write_keeps_claim_until_stale(client, monkeypatch):
    """A failed owner remains visible while an identical retry can recover it."""
    packet_id = "packet-write-fails"
    capture_id, packet = _ready(client, packet_id)
    deps = client.app_state
    record = deps.state.get_capture(capture_id)
    real_put = deps.storage.put_bytes

    def offline(*_args, **_kwargs):
        raise RuntimeError("storage offline")

    monkeypatch.setattr(deps.storage, "put_bytes", offline)
    with pytest.raises(RuntimeError):
        accept_packet(deps, record.model_copy(deep=True), json.dumps(packet).encode())
    monkeypatch.setattr(deps.storage, "put_bytes", real_put)

    raw = json.dumps(packet).encode()
    claim = deps.state.get_kv(f"accepted_packet:{capture_id}:0")
    assert claim and claim["packet_sha256"] == sha256_hex(raw)
    packet["createdAt"] = "2026-09-27T00:00:00.000Z"
    with pytest.raises(ApiFailure) as refused:
        accept_packet(deps, record.model_copy(deep=True), json.dumps(packet).encode())
    assert refused.value.status == 409
    now = deps.now()
    deps.clock = lambda: now + timedelta(minutes=5, seconds=1)
    corrected = accept_packet(deps, record.model_copy(deep=True), json.dumps(packet).encode())
    drain_finalize(deps)
    assert corrected["status"] == "processing"


@pytest.mark.parametrize("failure", ["save_extras", "put_capture"])
def test_finalize_failure_keeps_claim_for_a_different_packet_until_stale(client, monkeypatch, failure):
    import server.api.finalize as finalize_module

    packet_id = f"packet-{failure}"
    capture_id, packet = _ready(client, packet_id)
    deps = client.app_state
    raw = json.dumps(packet).encode()

    def offline(*_args, **_kwargs):
        raise RuntimeError(failure)

    with monkeypatch.context() as patch:
        target = deps.state if failure == "put_capture" else finalize_module
        patch.setattr(target, failure, offline)
        with pytest.raises(RuntimeError, match=failure):
            accept_packet(deps, deps.state.get_capture(capture_id), raw)

    # The packet bytes made it to storage, but acceptance was not persisted.
    assert deps.storage.get_bytes(f"captures/{capture_id}/packet.json") == raw
    assert deps.state.get_capture(capture_id).packet_sha256 is None
    packet["createdAt"] = "2026-09-27T00:00:00.000Z"
    corrected_raw = json.dumps(packet).encode()
    with pytest.raises(ApiFailure) as refused:
        accept_packet(deps, deps.state.get_capture(capture_id), corrected_raw)
    assert refused.value.status == 409
    now = deps.now()
    deps.clock = lambda: now + timedelta(minutes=5, seconds=1)
    corrected = accept_packet(deps, deps.state.get_capture(capture_id), corrected_raw)
    drain_finalize(deps)
    assert corrected["status"] == "processing"
    assert deps.state.get_capture(capture_id).packet_sha256 == sha256_hex(corrected_raw)


def test_failure_after_capture_persistence_keeps_claim_and_identical_replay(client, monkeypatch):
    import server.api.finalize as finalize_module

    capture_id, packet = _ready(client, "packet-persisted-failure")
    deps = client.app_state
    stale_record = deps.state.get_capture(capture_id)
    raw = json.dumps(packet).encode()

    def offline(*_args, **_kwargs):
        raise RuntimeError("event offline")

    with monkeypatch.context() as patch:
        patch.setattr(finalize_module, "_emit_validate", offline)
        with pytest.raises(RuntimeError, match="event offline"):
            accept_packet(deps, stale_record.model_copy(deep=True), raw)

    assert deps.state.get_capture(capture_id).packet_sha256 == sha256_hex(raw)
    assert deps.state.get_kv(f"accepted_packet:{capture_id}:0")["packet_sha256"] == sha256_hex(raw)
    packet["createdAt"] = "2026-09-27T00:00:00.000Z"
    with pytest.raises(ApiFailure) as refused:
        accept_packet(deps, stale_record, json.dumps(packet).encode())
    assert refused.value.status == 409
    assert refused.value.errors[0]["code"] == "packet_sha_conflict"
    replay = accept_packet(deps, deps.state.get_capture(capture_id), raw)
    drain_finalize(deps)
    assert replay["status"] == "processing"
    assert replay["runId"] == deps.state.get_capture(capture_id).run_id


@pytest.mark.parametrize("interruption", [CancelledError, SystemExit], ids=["cancelled", "crash"])
def test_interrupted_claim_can_be_superseded_only_after_staleness_bound(client, monkeypatch, interruption):
    capture_id, packet = _ready(client, "packet-interrupted")
    deps = client.app_state
    stale_record = deps.state.get_capture(capture_id)
    raw = json.dumps(packet).encode()
    now = datetime(2026, 10, 22, 19, 0, tzinfo=UTC)
    deps.clock = lambda: now

    def interrupted(*_args, **_kwargs):
        raise interruption()

    with monkeypatch.context() as patch:
        patch.setattr(deps.storage, "put_bytes", interrupted)
        with pytest.raises(interruption):
            accept_packet(deps, stale_record.model_copy(deep=True), raw)

    first_key = f"accepted_packet:{capture_id}:0"
    claim = deps.state.get_kv(first_key)
    assert claim["packet_sha256"] == sha256_hex(raw)
    assert claim["owner"]
    assert claim["claimed_at"] == "2026-10-22T19:00:00.000Z"
    assert deps.state.get_kv(f"{first_key}:committed") is None
    packet["createdAt"] = "2026-09-27T00:00:00.000Z"
    replacement = json.dumps(packet).encode()
    for elapsed in (timedelta(0), timedelta(minutes=5)):
        deps.clock = lambda elapsed=elapsed: now + elapsed
        with pytest.raises(ApiFailure) as refused:
            accept_packet(deps, stale_record.model_copy(deep=True), replacement)
        assert refused.value.status == 409
        assert deps.state.get_kv(f"accepted_packet:{capture_id}:1") is None

    deps.clock = lambda: now + timedelta(minutes=5, microseconds=1)
    accepted = accept_packet(deps, stale_record.model_copy(deep=True), replacement)
    drain_finalize(deps)
    assert accepted["status"] == "processing"
    assert deps.state.get_kv(first_key) == claim
    assert deps.state.get_kv(f"accepted_packet:{capture_id}:1")["packet_sha256"] == sha256_hex(replacement)
    assert deps.state.get_kv(f"accepted_packet:{capture_id}:1:committed")


def test_identical_retry_completes_failed_claim_without_deleting_it(client, monkeypatch):
    capture_id, packet = _ready(client, "packet-identical-retry")
    deps = client.app_state
    stale_record = deps.state.get_capture(capture_id)
    raw = json.dumps(packet).encode()

    def offline(*_args, **_kwargs):
        raise RuntimeError("storage offline")

    with monkeypatch.context() as patch:
        patch.setattr(deps.storage, "put_bytes", offline)
        with pytest.raises(RuntimeError, match="storage offline"):
            accept_packet(deps, stale_record.model_copy(deep=True), raw)

    claim_key = f"accepted_packet:{capture_id}:0"
    claim = deps.state.get_kv(claim_key)
    assert claim is not None
    accepted = accept_packet(deps, stale_record.model_copy(deep=True), raw)
    drain_finalize(deps)
    assert accepted["status"] == "processing"
    assert deps.state.get_kv(claim_key) == claim
    assert deps.state.get_kv(f"{claim_key}:committed")
    assert deps.state.get_kv(f"accepted_packet:{capture_id}:1") is None


def test_failed_owner_cannot_release_identical_inflight_writers_claim(client, monkeypatch):
    capture_id, packet = _ready(client, "packet-inflight-identical")
    deps = client.app_state
    stale_record = deps.state.get_capture(capture_id)
    raw = json.dumps(packet).encode()
    packet["createdAt"] = "2026-09-27T00:00:00.000Z"
    different = json.dumps(packet).encode()
    owner_writing, retry_writing = Event(), Event()
    fail_owner, finish_retry = Event(), Event()
    real_put = deps.storage.put_bytes

    def gated_put(*args, **kwargs):
        if current_thread().name.startswith("claim-owner"):
            owner_writing.set()
            assert fail_owner.wait(5), "owner was never released"
            raise RuntimeError("owner write failed")
        if current_thread().name.startswith("claim-retry"):
            retry_writing.set()
            assert finish_retry.wait(5), "retry was never released"
        return real_put(*args, **kwargs)

    monkeypatch.setattr(deps.storage, "put_bytes", gated_put)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="claim-owner") as owner_pool:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="claim-retry") as retry_pool:
            try:
                owner = owner_pool.submit(accept_packet, deps, stale_record.model_copy(deep=True), raw)
                assert owner_writing.wait(5), "owner did not reach storage"
                retry = retry_pool.submit(accept_packet, deps, stale_record.model_copy(deep=True), raw)
                assert retry_writing.wait(5), "identical retry did not reach storage"
                fail_owner.set()
                with pytest.raises(RuntimeError, match="owner write failed"):
                    owner.result(timeout=5)
                # B is still writing after A failed. A must not open a hole for C.
                with pytest.raises(ApiFailure) as refused:
                    accept_packet(deps, stale_record.model_copy(deep=True), different)
                assert refused.value.status == 409
            finally:
                fail_owner.set()
                finish_retry.set()
            assert retry.result(timeout=5)["status"] == "processing"
    drain_finalize(deps)
    assert deps.storage.get_bytes(f"captures/{capture_id}/packet.json") == raw
    assert deps.state.get_capture(capture_id).packet_sha256 == sha256_hex(raw)


def test_committed_generation_never_expires_even_for_stale_capture_view(client):
    capture_id, packet = _ready(client, "packet-committed-generation")
    deps = client.app_state
    stale_record = deps.state.get_capture(capture_id)
    raw = json.dumps(packet).encode()
    accept_packet(deps, stale_record.model_copy(deep=True), raw)
    drain_finalize(deps)
    now = deps.now()
    deps.clock = lambda: now + timedelta(days=30)
    packet["createdAt"] = "2026-09-27T00:00:00.000Z"
    with pytest.raises(ApiFailure) as refused:
        accept_packet(deps, stale_record, json.dumps(packet).encode())
    assert refused.value.status == 409
    assert deps.state.get_kv(f"accepted_packet:{capture_id}:0:committed")
    assert deps.state.get_kv(f"accepted_packet:{capture_id}:1") is None
