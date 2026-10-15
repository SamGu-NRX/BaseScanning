"""HSS_AUTH_MODE is configuration. The dev deploy defaults to none."""

from __future__ import annotations

import pytest

from server.common.config import Settings


def test_dev_defaults_to_none_until_auth_mode_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HSS_AUTH_MODE", raising=False)
    monkeypatch.delenv("SMP_ENVIRONMENT", raising=False)
    assert Settings(SMP_ENVIRONMENT="dev").HSS_AUTH_MODE == "none"
    assert Settings(SMP_ENVIRONMENT="prod").HSS_AUTH_MODE == "static"
    assert Settings(SMP_ENVIRONMENT="dev", HSS_AUTH_MODE="static").HSS_AUTH_MODE == "static"
    assert Settings(SMP_ENVIRONMENT="dev", HSS_AUTH_MODE="jwt").HSS_AUTH_MODE == "jwt"
    monkeypatch.setenv("SMP_ENVIRONMENT", "dev")
    monkeypatch.setenv("HSS_AUTH_MODE", "static")
    assert Settings().HSS_AUTH_MODE == "static"
    monkeypatch.delenv("HSS_AUTH_MODE")
    assert Settings().HSS_AUTH_MODE == "none"
