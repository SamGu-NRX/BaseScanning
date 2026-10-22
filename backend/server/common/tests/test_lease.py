from __future__ import annotations

import threading

import pytest

from server.common.lease import LeaseBusy, LeaseLost, with_lease
from server.common.backends.state_memory import MemoryState


def test_lease_renews_while_callback_is_longer_than_expiry() -> None:
    state = MemoryState()
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()
    errors: list[BaseException] = []

    def first() -> None:
        entered.set()
        assert release.wait(2)

    def second() -> None:
        second_entered.set()

    def run_first() -> None:
        try:
            with_lease(state, "completion", first, wait_s=0.2, lease_s=0.05, strict=True)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_first)
    worker.start()
    assert entered.wait(1)
    with pytest.raises(LeaseBusy):
        with_lease(state, "completion", second, wait_s=0.05, lease_s=0.05, strict=True)
    assert not second_entered.is_set()
    release.set()
    worker.join(2)
    assert errors == []


def test_expired_holder_cannot_publish_over_second_owner() -> None:
    state = MemoryState()
    entered = threading.Event()
    let_first_finish = threading.Event()
    second_entered = threading.Event()
    first_errors: list[BaseException] = []

    def first(guard) -> None:
        entered.set()
        assert let_first_finish.wait(2)
        guard.assert_held()

    def run_first() -> None:
        try:
            with_lease(state, "completion", first, wait_s=0.2, lease_s=60, strict=True, pass_guard=True)
        except BaseException as exc:
            first_errors.append(exc)

    worker = threading.Thread(target=run_first)
    worker.start()
    assert entered.wait(1)
    state.delete_kv("lease:completion")
    with_lease(
        state,
        "completion",
        lambda: second_entered.set(),
        wait_s=0.2,
        lease_s=60,
        strict=True,
    )
    assert second_entered.is_set()
    let_first_finish.set()
    worker.join(2)
    assert len(first_errors) == 1
    assert isinstance(first_errors[0], LeaseLost)
