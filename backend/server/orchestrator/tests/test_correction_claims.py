"""Exactly one run owns each correction claim generation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from server.orchestrator.correction_claims import claim_answer, claim_is_pending, latest_claim
from server.orchestrator.tests.conftest import build_services
from server.orchestrator.tests.test_greptile_followup import _run


@pytest.mark.parametrize("backend", ["memory", "modal_dict"])
def test_competing_runs_get_one_owner_per_generation(tmp_path, monkeypatch, backend):
    services, _, _ = build_services(tmp_path, backend)
    state = services.state
    runs = [_run(services, name) for name in ("run_a", "run_b", "run_c")]
    for run in runs:
        state.put_run(run)
    original = state.put_if_absent
    first_slot = "claim:cap_test:answer:1"
    gate = Barrier(2)
    attempted = []

    def contend(key, row):
        if key == first_slot:
            attempted.append(row["runId"])
            gate.wait(timeout=5)
        return original(key, row)

    with monkeypatch.context() as patch:
        patch.setattr(state, "put_if_absent", contend)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim_answer, state, run, "answer") for run in runs[:2]]
            owners = [future.result(timeout=10) for future in futures]
    assert sorted(attempted) == ["run_a", "run_b"]
    assert owners[0] == owners[1]
    assert state.get_kv("claim:cap_test:answer:2") is None
    assert not claim_is_pending(state, "cap_test", "answer")
    failed = state.get_run(owners[0]["runId"])
    failed.status = "failed"
    state.put_run(failed)
    assert claim_is_pending(state, "cap_test", "answer")
    second = claim_answer(state, runs[2], "answer")
    assert second == {"runId": "run_c", "generation": 2}
    assert state.get_kv(first_slot) == owners[0]
    assert latest_claim(state, "cap_test", "answer") == second
    assert not claim_is_pending(state, "cap_test", "answer")


def test_delivered_owner_cannot_be_taken_over(tmp_path):
    services, _, _ = build_services(tmp_path, "memory")
    first = _run(services, "run_a", status="done")
    second = _run(services, "run_b")
    for run in (first, second):
        services.state.put_run(run)
    owner = claim_answer(services.state, first, "answer")
    assert claim_answer(services.state, second, "answer") == owner
    assert services.state.get_kv("claim:cap_test:answer:2") is None
