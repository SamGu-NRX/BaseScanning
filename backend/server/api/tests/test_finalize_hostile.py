"""Finalize input that the schema alone let through, found by the Z10 audit probes."""

from __future__ import annotations

import json
import threading
from datetime import timedelta

import pytest

from server.api.deps import drain_finalize, isoformat
from server.api.errors import ApiFailure
from server.api.finalize import accept_packet
from server.api.tests.conftest import photo_packet, sha256_hex
from server.api.tests.test_ingest import _create, _put_commit
from server.common.deploy_switch import web_function_timeout_s

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


def test_a_same_packet_retry_on_another_container_writes_nothing(client, monkeypatch):
    """Review of #327 at 3c24e69a: the retry rewrote packet.json while the first
    finalize worker read it. The volume backend truncates before writing, so the
    worker could read an empty file and fail with JSONDecodeError. Counting writes
    makes the race deterministic: only the claimant may write or schedule."""
    packet_id = "packet-retry-no-write"
    capture_id, packet = _ready(client, packet_id)
    deps = client.app_state
    first_view = deps.state.get_capture(capture_id)
    second_view = first_view.model_copy(deep=True)
    raw = json.dumps(packet).encode()
    key = f"captures/{capture_id}/packet.json"
    writes: list[str] = []
    real_put = deps.storage.put_bytes

    def counting_put(write_key, data, content_type):
        writes.append(write_key)
        return real_put(write_key, data, content_type)

    monkeypatch.setattr(deps.storage, "put_bytes", counting_put)
    jobs_before = len(deps.finalize_jobs)
    first = accept_packet(deps, first_view, raw)
    second = accept_packet(deps, second_view, raw)
    jobs = len(deps.finalize_jobs) - jobs_before
    drain_finalize(deps)

    assert writes.count(key) == 1
    assert jobs == 1
    assert second == first
    assert deps.storage.get_bytes(key) == raw
    assert deps.state.get_capture(capture_id).status == "processing"


def _plant_claim(deps, capture_id: str, raw: bytes, age_s: float) -> None:
    """A claim whose request died before it wrote packet.json or scheduled anything."""
    claimed_at = deps.now() - timedelta(seconds=age_s)
    deps.state.put_kv(
        f"accepted_packet:{capture_id}:-",
        {"packet_sha256": sha256_hex(raw), "claimed_at": isoformat(claimed_at), "token": "clm_dead"},
    )


def _count_writes_and_jobs(deps, monkeypatch, capture_id: str):
    key = f"captures/{capture_id}/packet.json"
    writes: list[str] = []
    real_put = deps.storage.put_bytes

    def counting_put(write_key, data, content_type):
        writes.append(write_key)
        return real_put(write_key, data, content_type)

    monkeypatch.setattr(deps.storage, "put_bytes", counting_put)
    jobs_before = len(deps.finalize_jobs)
    return lambda: (writes.count(key), len(deps.finalize_jobs) - jobs_before)


STALE_S = web_function_timeout_s() + 60


def test_a_retry_takes_over_an_abandoned_claim(client, monkeypatch):
    """Astra review at 3864c436: a claimant that died between put_if_absent and the
    packet write left every same-digest retry answering processing with nothing
    scheduled, so the capture stayed uploading forever."""
    packet_id = "packet-abandoned-claim"
    capture_id, packet = _ready(client, packet_id)
    deps = client.app_state
    raw = json.dumps(packet).encode()
    _plant_claim(deps, capture_id, raw, age_s=STALE_S)
    counts = _count_writes_and_jobs(deps, monkeypatch, capture_id)

    response = accept_packet(deps, deps.state.get_capture(capture_id), raw)
    writes, jobs = counts()
    drain_finalize(deps)

    assert (writes, jobs) == (1, 1)
    assert response["status"] == "processing"
    assert deps.storage.get_bytes(f"captures/{capture_id}/packet.json") == raw
    assert deps.state.get_capture(capture_id).status == "processing"


def test_a_retry_leaves_a_fresh_claim_alone(client, monkeypatch):
    """Guard, not failing-first: 3864c436 never schedules on a retry, so it passes
    there too. It fails if the bound is dropped (checked by setting it to -1)."""
    packet_id = "packet-fresh-claim"
    capture_id, packet = _ready(client, packet_id)
    deps = client.app_state
    raw = json.dumps(packet).encode()
    _plant_claim(deps, capture_id, raw, age_s=5)
    counts = _count_writes_and_jobs(deps, monkeypatch, capture_id)

    response = accept_packet(deps, deps.state.get_capture(capture_id), raw)

    assert counts() == (0, 0)
    assert response["status"] == "processing"


def test_concurrent_takeovers_schedule_once(client, monkeypatch):
    packet_id = "packet-two-takeovers"
    capture_id, packet = _ready(client, packet_id)
    deps = client.app_state
    raw = json.dumps(packet).encode()
    _plant_claim(deps, capture_id, raw, age_s=STALE_S)
    counts = _count_writes_and_jobs(deps, monkeypatch, capture_id)
    # Both retries must have read the abandoned claim before either tries to take it.
    both_read = threading.Barrier(2, timeout=10)
    real_insert = deps.state.put_if_absent

    def insert_after_both_read(key, value):
        if ":takeover:" in key:
            both_read.wait()
        return real_insert(key, value)

    monkeypatch.setattr(deps.state, "put_if_absent", insert_after_both_read)
    record = deps.state.get_capture(capture_id)
    replies: list[dict] = []
    threads = [
        threading.Thread(target=lambda: replies.append(accept_packet(deps, record.model_copy(deep=True), raw)))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    writes, jobs = counts()
    drain_finalize(deps)

    assert len(replies) == 2
    assert (writes, jobs) == (1, 1)
    assert {reply["runId"] for reply in replies} == {replies[0]["runId"]}
    assert deps.state.get_capture(capture_id).status == "processing"


def test_a_failed_packet_write_releases_the_claim(client, monkeypatch):
    """Guard for the claim, not failing-first: alpha has no claim to release."""
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

    packet["createdAt"] = "2026-09-27T00:00:00.000Z"
    corrected = accept_packet(deps, record.model_copy(deep=True), json.dumps(packet).encode())
    drain_finalize(deps)
    assert corrected["status"] == "processing"
