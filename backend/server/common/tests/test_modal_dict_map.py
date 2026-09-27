"""modal.Dict misses must return the default, not raise."""

from server.common.backends.state_modal_dict import _RemoteMap


class _Miss:
    def __init__(self) -> None:
        self.store: dict = {}

    def get(self, key):
        if key not in self.store:
            raise KeyError(key)
        return self.store[key]

    def __contains__(self, key) -> bool:
        return key in self.store

    def __setitem__(self, key, value) -> None:
        self.store[key] = value


def test_missing_key_returns_default() -> None:
    remote = _RemoteMap(_Miss())
    assert remote.get("absent") is None
    assert "absent" not in remote
    remote["present"] = {"ok": True}
    assert remote.get("present") == {"ok": True}
