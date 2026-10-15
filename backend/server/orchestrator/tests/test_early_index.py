"""A new capture's early index is a point read. Commit must not scan the Dict."""

from __future__ import annotations

from server.common.backends.state_modal_dict import ModalDictState
from server.common.tests.test_modal_dict_persist import _capture
from server.orchestrator.early.index import early_keys


class _NoScan(dict):
    def keys(self):
        raise AssertionError("full dict scan")


def test_early_keys_do_not_scan_after_create() -> None:
    state = ModalDictState(_NoScan())
    state.put_capture(_capture("cap_new"))
    assert early_keys(state, "cap_new") == []
